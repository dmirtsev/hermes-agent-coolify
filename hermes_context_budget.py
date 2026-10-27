"""Full-request admission. Counting is local and never invokes a paid model.

The committed route certificate is deliberately separate from the tokenizer:
possessing an encoder does not prove compatibility with a provider's framing.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

CONTRACT = "hermes-context-budget-v1"
OUTPUT_TOKENS = 8192
PRICE_CAPS = {
    "openai/gpt-6-luna": {"prompt": 0.125, "completion": 0.5},
    "deepseek/deepseek-v4-flash": {"prompt": 0.1, "completion": 0.5},
}
ASSETS = Path(os.environ.get("HERMES_TOKENIZER_ASSETS", "/opt/hermes/tokenizers"))
CERTIFICATES = Path(__file__).with_name("context_budget_routes.json")


class ContextBudgetError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _deepseek_encoder():
    from tokenizers import Tokenizer
    spec = importlib.util.spec_from_file_location("dsv4_reference", ASSETS / "encoding_dsv4.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return Tokenizer.from_file(str(ASSETS / "deepseek-tokenizer.json")), module


def count_messages(model, messages, response_format=None):
    if (not isinstance(messages, list) or not messages or
            any(not isinstance(message, dict) or set(message) - {"role", "content"} or
                message.get("role") not in {"system", "user", "assistant"} or
                not isinstance(message.get("content"), str) for message in messages)):
        raise ContextBudgetError("hermes_counter_unsupported_message_shape")
    if model == "deepseek/deepseek-v4-flash":
        tokenizer, reference = _deepseek_encoder()
        # The published reference includes role delimiters and generation prefix.
        rendered = reference.encode_messages(deepcopy(messages), thinking_mode="thinking", reasoning_effort="high")
        return len(tokenizer.encode(rendered, add_special_tokens=False).ids)
    if model == "openai/gpt-6-luna":
        import tiktoken
        encoder = tiktoken.get_encoding("o200k_base")
        # Reference estimate only until a route certificate validates this exact
        # two-text-message request shape, framing and JSON mode independently.
        return 3 + sum(3 + len(encoder.encode(m["role"], disallowed_special=())) +
                       len(encoder.encode(m["content"], disallowed_special=())) for m in messages)
    raise ContextBudgetError("hermes_counter_model_unavailable")


def _unpack_rows(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or value.get("format") != "typed_rows_v1":
        raise ContextBudgetError("hermes_context_rows_invalid")
    layouts, rows = value.get("layouts"), value.get("rows")
    if not isinstance(layouts, list) or not isinstance(rows, list) or value.get("count") != len(rows):
        raise ContextBudgetError("hermes_context_rows_invalid")
    result = []
    for row in rows:
        if not isinstance(row, list) or not row or type(row[0]) is not int or not 0 <= row[0] < len(layouts):
            raise ContextBudgetError("hermes_context_rows_invalid")
        columns = layouts[row[0]]
        if (not isinstance(columns, list) or len(columns) != len(row) - 1 or
                any(not isinstance(c, str) for c in columns) or len(columns) != len(set(columns))):
            raise ContextBudgetError("hermes_context_rows_invalid")
        result.append(dict(zip(columns, row[1:])))
    return result


def _verify_embedded_method(body):
    reading = body.get("tp_managed_method_context")
    if reading is None:
        return
    try:
        user = json.loads(body["messages"][1]["content"])
        expected = {key: reading[key] for key in ("profile", "selection", "resolution", "knowledge")}
        calculation = dict(user["context"]["transitPeriod"])
        calculation["facts"] = _unpack_rows(calculation["facts"])
        if user["context"]["method"] != expected or calculation != reading["calculation"]:
            raise ContextBudgetError("hermes_context_method_payload_mismatch")
        history = _unpack_rows(user["history"])
        cursor = 0
        for item in reading["memory_context"]["items"]:
            while cursor < len(history) and history[cursor] != {"role": item["role"], "content": item["content"]}:
                cursor += 1
            if cursor == len(history):
                raise ContextBudgetError("hermes_context_method_history_mismatch")
            cursor += 1
    except (ValueError, TypeError, KeyError, IndexError) as error:
        if isinstance(error, ContextBudgetError):
            raise
        raise ContextBudgetError("hermes_context_method_payload_invalid") from error


@lru_cache(maxsize=1)
def _load_certificates():
    profiles = json.loads(CERTIFICATES.read_text())
    for profile in profiles.values():
        name = profile.get("evidenceFile")
        if not isinstance(name, str) or Path(name).name != name:
            raise ContextBudgetError("hermes_counter_evidence_missing")
        evidence = CERTIFICATES.with_name(name).read_bytes()
        if hashlib.sha256(evidence).hexdigest() != profile.get("evidenceSha256"):
            raise ContextBudgetError("hermes_counter_evidence_mismatch")
    return profiles


def prepare_admission(body, certificates=None, counter=count_messages):
    budget = body.get("tp_context_budget")
    if budget is None:
        if body.get("tp_preflight_only"):
            raise ContextBudgetError("hermes_context_budget_required")
        return None
    if (not isinstance(budget, dict) or budget.get("contract") != CONTRACT or
            type(budget.get("inputBudget")) is not int or not 1000 <= budget["inputBudget"] <= 1_000_000 or
            budget.get("profile") not in {"natal", "natal_transit"} or
            body.get("tp_answer_format") != "hermes_managed_v1" or
            body.get("tools") or body.get("stream")):
        raise ContextBudgetError("hermes_context_budget_invalid")
    model = body.get("model")
    expected_code = {"openai/gpt-6-luna": "gpt_luna", "deepseek/deepseek-v4-flash": "deepseek_flash"}.get(model)
    if expected_code is None or body.get("tp_dialogue_model") != expected_code:
        raise ContextBudgetError("hermes_context_dialogue_route_required")
    _verify_embedded_method(body)
    profiles = certificates if certificates is not None else _load_certificates()
    profile = profiles.get(model)
    if not profile:
        raise ContextBudgetError("hermes_context_route_unavailable")
    count = counter(model, body.get("messages"), body.get("response_format"))
    if type(count) is not int or count < 0:
        raise ContextBudgetError("hermes_counter_invalid")
    # A calibration may only add a conservative correction, never subtract.
    count += int(profile.get("framingUpperCorrection", 0))
    margin = max(4000, math.ceil(budget["inputBudget"] * 0.05))
    accuracy = profile["accuracy"]
    reason = None
    if accuracy not in {"provider_verified", "calibrated"} or not profile.get("evidenceSha256"):
        reason = "hermes_context_counter_unverified"
    elif count > budget["inputBudget"]:
        reason = "hermes_context_input_overflow"
    elif count + OUTPUT_TOKENS + margin > profile["contextWindow"]:
        reason = "hermes_context_window_overflow"
    return {
        "contract": CONTRACT, "model": model, "route": profile["route"],
        "counter": profile["counter"], "counterRevision": profile["counterRevision"],
        "accuracy": accuracy, "inputTokens": count, "inputBudget": budget["inputBudget"],
        "completionTokens": OUTPUT_TOKENS, "marginTokens": margin,
        "contextWindow": profile["contextWindow"], "messagesSha256": digest(body["messages"]),
        "admitted": reason is None, "reason": reason,
    }


def require_admission(body, receipt):
    if receipt is None:
        return
    if not receipt["admitted"]:
        raise ContextBudgetError(receipt["reason"])
    if body.get("tp_context_admission") != receipt:
        raise ContextBudgetError("hermes_context_admission_mismatch")


def install_context_budget(agent, body):
    if body.get("tp_context_budget") is not None:
        agent._tp_context_budget_body = deepcopy(body)
        agent._tp_context_model_calls = 0
        # Full history/facts may be rejected, never automatically summarized.
        agent.compression_enabled = False
        overrides = deepcopy(getattr(agent, "request_overrides", {}) or {})
        provider = overrides.setdefault("extra_body", {}).setdefault("provider", {})
        provider["max_price"] = PRICE_CAPS[body["model"]]
        agent.request_overrides = overrides


def readable_managed_part(content, managed_json):
    """Only a whole validated JSON object is eligible to stop auto-replacement.
    Cabinet remains the authority for fact/reference validation and publication.
    A malformed candidate still gets a bounded fresh-JSON repair attempt.
    """
    if not isinstance(content, str) or not content.strip():
        return False
    if not managed_json:
        return not content.lstrip().startswith(("{", "[", "```"))
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return False
    if not isinstance(value, dict):
        return False
    if value.get("mode") == "facts":
        items = value.get("items")
        return isinstance(items, list) and bool(items) and all(
            isinstance(item, dict) and isinstance(item.get("refs"), list) and bool(item["refs"])
            and all(isinstance(ref, str) for ref in item["refs"]) for item in items)
    paragraphs = value.get("paragraphs")
    return value.get("mode") == "interpretation" and isinstance(paragraphs, list) and bool(paragraphs) and all(
        isinstance(p, dict) and isinstance(p.get("text"), str) and bool(p["text"].strip()) and
        isinstance(p.get("refs"), list) and all(isinstance(ref, str) for ref in p["refs"]) for p in paragraphs)


def admit_provider_call(agent, api_kwargs):
    original = getattr(agent, "_tp_context_budget_body", None)
    if original is None:
        return
    if (api_kwargs.get("model") != original["model"] or api_kwargs.get("tools") or
            api_kwargs.get("max_tokens", api_kwargs.get("max_completion_tokens")) != OUTPUT_TOKENS):
        raise ContextBudgetError("hermes_context_provider_contract_changed")
    extra = api_kwargs.get("extra_body") or {}
    expected_provider = ({"only": ["openai"], "allow_fallbacks": False, "max_price": PRICE_CAPS[original["model"]]}
        if original["model"] == "openai/gpt-6-luna" else {
            "order": ["deepinfra/fp8", "gmicloud/fp8"], "only": ["deepinfra/fp8", "gmicloud/fp8"],
            "allow_fallbacks": True, "max_price": PRICE_CAPS[original["model"]]})
    if (extra.get("provider") != expected_provider or extra.get("service_tier") != "default"
            or extra.get("reasoning", {}).get("effort") != "high"
            or set(extra) - {"provider", "service_tier", "reasoning", "usage"}
            or api_kwargs.get("response_format") != original.get("response_format")):
        raise ContextBudgetError("hermes_context_provider_route_changed")
    calls = getattr(agent, "_tp_context_model_calls", 0)
    if calls >= 3:
        raise ContextBudgetError("hermes_context_automatic_attempt_limit")
    if calls == 0 and api_kwargs.get("messages") != original["messages"]:
        raise ContextBudgetError("hermes_context_provider_messages_changed")
    actual = {**original, "messages": api_kwargs.get("messages"),
              "response_format": api_kwargs.get("response_format")}
    receipt = prepare_admission(actual)
    if not receipt or not receipt["admitted"]:
        raise ContextBudgetError((receipt or {}).get("reason", "hermes_context_not_admitted"))
    # This guard is immediately before transport, after middleware/overrides.
    # Failed admission is not counted as a dispatched provider attempt.
    agent._tp_context_model_calls = calls + 1
