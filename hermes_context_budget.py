"""Full-request admission. Counting is local and never invokes a paid model.

The committed route certificate is deliberately separate from the tokenizer:
possessing an encoder does not prove compatibility with a provider's framing.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import math
import os
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

if __package__:
    from .hermes_context_rows import ContextRowsError, unpack_context_rows
    from .hermes_managed_validation import (ManagedValidationContextError,
        prepare_managed_transit_context, managed_transit_issues, managed_transit_repair_prompt)
else:
    from hermes_context_rows import ContextRowsError, unpack_context_rows
    from hermes_managed_validation import (ManagedValidationContextError,
        prepare_managed_transit_context, managed_transit_issues, managed_transit_repair_prompt)

CONTRACT = "hermes-context-budget-v1"
OUTPUT_TOKENS = 8192
PRICE_CAPS = {
    "openai/gpt-6-luna": {"prompt": 0.125, "completion": 0.5},
    "deepseek/deepseek-v4-flash": {"prompt": 0.1, "completion": 0.5},
    # Ceiling in USD/M tokens, kept separate from the provider's live tariff.
    "openai/gpt-6.1-terra": {"prompt": 2.5, "completion": 7.5},
    "deepseek/deepseek-v4.1-flash": {"prompt": 0.2, "completion": 0.6},
}
ASSETS = Path(os.environ.get("HERMES_TOKENIZER_ASSETS", "/opt/hermes/tokenizers"))
CERTIFICATES = Path(__file__).with_name("context_budget_routes.json")


class ContextBudgetError(ValueError):
    pass


# One implementation for admission receipts and durable request digests.
if __package__:
    from .durable_accounting import canonical_request_payload as canonical
else:
    from hermes_durable_accounting import canonical_request_payload as canonical


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _deepseek_encoder():
    from tokenizers import Tokenizer
    spec = importlib.util.spec_from_file_location("dsv4_reference", ASSETS / "encoding_dsv4.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return Tokenizer.from_file(str(ASSETS / "deepseek-tokenizer.json")), module


@lru_cache(maxsize=1)
def _deepseek_v41_encoder():
    from tokenizers import Tokenizer
    spec = importlib.util.spec_from_file_location("dsv41_reference", ASSETS / "encoding_v41.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return Tokenizer.from_file(str(ASSETS / "deepseek-v41-tokenizer.json")), module


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
    if model == "deepseek/deepseek-v4.1-flash":
        if ([m["role"] for m in messages] not in (["system", "user"], ["system", "user", "user"])
                or response_format not in (None, {"type": "json_object"})):
            raise ContextBudgetError("hermes_counter_uncalibrated_deepseek_flash_plus_shape")
        tokenizer, reference = _deepseek_v41_encoder()
        rendered = reference.encode_messages(deepcopy(messages), thinking_mode="thinking",
                                              reasoning_effort="high")
        return len(tokenizer.encode(rendered, add_special_tokens=False).ids)
    if model == "openai/gpt-6-luna":
        import tiktoken
        encoder = tiktoken.get_encoding("o200k_base")
        # Reference estimate only until a route certificate validates this exact
        # two-text-message request shape, framing and JSON mode independently.
        return 3 + sum(3 + len(encoder.encode(m["role"], disallowed_special=())) +
                       len(encoder.encode(m["content"], disallowed_special=())) for m in messages)
    if model == "openai/gpt-6.1-terra":
        # Independently calibrated Terra framing; see its own route evidence.
        if ([m["role"] for m in messages] not in (["system", "user"], ["system", "user", "user"])
                or response_format not in (None, {"type": "json_object"})):
            raise ContextBudgetError("hermes_counter_uncalibrated_terra_shape")
        import tiktoken
        encoder = tiktoken.get_encoding("o200k_base")
        return 3 + sum(3 + len(encoder.encode(m["role"], disallowed_special=())) +
                       len(encoder.encode(m["content"], disallowed_special=())) for m in messages)
    raise ContextBudgetError("hermes_counter_model_unavailable")


def _unpack_rows(value):
    try:
        return unpack_context_rows(value)
    except ContextRowsError as error:
        raise ContextBudgetError(str(error)) from None


def _verify_embedded_method(body):
    encoding = body.get("tp_context_encoding")
    if encoding not in (None, "period_lossless_v2"):
        raise ContextBudgetError("hermes_context_encoding_invalid")
    reading = body.get("tp_managed_method_context")
    if reading is None:
        if encoding is not None:
            raise ContextBudgetError("hermes_context_period_refs_invalid")
        return
    try:
        user = json.loads(body["messages"][1]["content"])
        expected = {key: reading[key] for key in ("profile", "selection", "resolution", "knowledge")}
        calculation = dict(user["context"]["transitPeriod"])
        packed = calculation["facts"]
        is_v2 = isinstance(packed, dict) and packed.get("format") == "period_facts_v2"
        if is_v2 != (encoding == "period_lossless_v2"):
            raise ContextBudgetError("hermes_context_encoding_invalid")
        calculation["facts"] = _unpack_rows(packed)
        if user["context"]["method"] != expected or calculation != reading["calculation"]:
            raise ContextBudgetError("hermes_context_method_payload_mismatch")
        if is_v2:
            statements = _unpack_rows(user["facts"]["statements"])
            expected_refs = calculation["factRefs"]
            ids = [fact.get("fact_id", fact.get("id")) for fact in calculation["facts"]]
            if (not expected_refs or ids != expected_refs
                    or any(not isinstance(ref, str) or not ref for ref in ids)
                    or len(set(ids)) != len(ids)):
                raise ContextBudgetError("hermes_context_period_refs_invalid")
            refs = [statement.get("ref") for statement in statements]
            if (any(not isinstance(ref, str) or not re.fullmatch(r"s[1-9]\d*", ref) for ref in refs)
                    or len(set(refs)) != len(refs)):
                raise ContextBudgetError("hermes_context_period_refs_invalid")
            period_statements = [statement for statement in statements
                                 if statement.get("fromLayerId") == "transit_period"
                                 and statement.get("aspectScope") == "between_layers"]
            if ([statement.get("sourceRef") for statement in period_statements] != expected_refs
                    or any(statement.get("status") != "present" or not statement.get("toLayerId")
                           or "text" in statement or "phrase" in statement for statement in period_statements)
                    or any("sourceRef" in statement for statement in statements if statement not in period_statements)):
                raise ContextBudgetError("hermes_context_period_refs_invalid")
        history = _unpack_rows(user["history"])
        cursor = 0
        for item in reading["memory_context"]["items"]:
            while cursor < len(history) and history[cursor] != {"role": item["role"], "content": item["content"]}:
                cursor += 1
            if cursor == len(history):
                raise ContextBudgetError("hermes_context_method_history_mismatch")
            cursor += 1
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
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
    expected_code = {"openai/gpt-6-luna": "gpt_luna", "deepseek/deepseek-v4-flash": "deepseek_flash", "openai/gpt-6.1-terra": "gpt_terra", "deepseek/deepseek-v4.1-flash": "deepseek_flash_plus"}.get(model)
    if expected_code is None or body.get("tp_dialogue_model") != expected_code:
        raise ContextBudgetError("hermes_context_dialogue_route_required")
    _verify_embedded_method(body)
    try:
        prepare_managed_transit_context(body)
    except ManagedValidationContextError as error:
        raise ContextBudgetError(str(error)) from None
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
        try:
            agent._tp_managed_transit_context = prepare_managed_transit_context(body)
        except ManagedValidationContextError as error:
            raise ContextBudgetError(str(error)) from None
        # Full history/facts may be rejected, never automatically summarized.
        agent.compression_enabled = False
        overrides = deepcopy(getattr(agent, "request_overrides", {}) or {})
        provider = overrides.setdefault("extra_body", {}).setdefault("provider", {})
        provider["max_price"] = PRICE_CAPS[body["model"]]
        agent.request_overrides = overrides
        # A stale/unknown upstream metadata cache must not impose a smaller
        # window than the already validated route receipt.
        window = (body.get("tp_context_admission") or {}).get("contextWindow")
        if type(window) is int and window > OUTPUT_TOKENS:
            agent._config_context_length = window
            compressor = getattr(agent, "context_compressor", None)
            if compressor is not None:
                compressor.context_length = window


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


def managed_completion_action(agent, content):
    """One outcome for both normal and length finish; never starts transport."""
    context = getattr(agent, "_tp_managed_transit_context", None)
    if context is None:
        return "pass", ""
    try:
        issues = managed_transit_issues(content, context)
    except Exception:
        return "fail", "managed_transit_validation_unavailable"
    if not issues:
        return "pass", ""
    logging.getLogger(__name__).warning(
        "Managed transit validation attempt=%s issues=%s",
        getattr(agent, "_tp_context_model_calls", 0), "|".join(issues))
    if getattr(agent, "_tp_context_model_calls", 0) >= 3:
        return "fail", "managed_transit_validation_exhausted"
    feedback = managed_transit_repair_prompt(issues, context)
    agent._tp_managed_repair_feedback = feedback
    return "retry", feedback


def admit_provider_call(agent, api_kwargs):
    original = getattr(agent, "_tp_context_budget_body", None)
    if original is None:
        return
    calls = getattr(agent, "_tp_context_model_calls", 0)
    if api_kwargs.get("model") != original["model"] or api_kwargs.get("tools"):
        raise ContextBudgetError("hermes_context_provider_contract_changed")
    if (calls and getattr(agent, "_tp_managed_transit_context", None) is not None
            and getattr(agent, "_tp_managed_repair_feedback", None)):
        # Length recovery may grow the upstream output limit. Every fresh
        # managed repair uses the same admitted 8192-token output contract.
        key = getattr(agent, "_tp_context_output_parameter", None)
        if key not in {"max_tokens", "max_completion_tokens"}:
            raise ContextBudgetError("hermes_context_provider_contract_changed")
        api_kwargs.pop("max_completion_tokens" if key == "max_tokens" else "max_tokens", None)
        api_kwargs[key] = OUTPUT_TOKENS
    if (
            api_kwargs.get("max_tokens", api_kwargs.get("max_completion_tokens")) != OUTPUT_TOKENS):
        raise ContextBudgetError("hermes_context_provider_contract_changed")
    if not calls:
        agent._tp_context_output_parameter = "max_tokens" if "max_tokens" in api_kwargs else "max_completion_tokens"
    extra = api_kwargs.get("extra_body") or {}
    expected_provider = ({"only": ["openai"], "allow_fallbacks": False, "max_price": PRICE_CAPS[original["model"]]}
        if original["model"] in {"openai/gpt-6-luna", "openai/gpt-6.1-terra"} else {
            "order": ["deepinfra/fp8"], "only": ["deepinfra/fp8"],
            "allow_fallbacks": False, "max_price": PRICE_CAPS[original["model"]]})
    expected_effort = "medium" if original["model"] == "openai/gpt-6.1-terra" else "high"
    if original["model"] in {"openai/gpt-6.1-terra", "deepseek/deepseek-v4.1-flash"}:
        expected_provider["require_parameters"] = True
    if (extra.get("provider") != expected_provider or extra.get("service_tier") != "default"
            or extra.get("reasoning", {}).get("effort") != expected_effort
            # The native OpenRouter profile adds accounting metadata. It may
            # identify this exact isolated operation, never another session.
            or ("session_id" in extra and extra["session_id"] != getattr(agent, "session_id", None))
            or set(extra) - {"provider", "service_tier", "reasoning", "usage", "session_id"}
            or api_kwargs.get("response_format") != original.get("response_format")):
        raise ContextBudgetError("hermes_context_provider_route_changed")
    if calls >= 3:
        raise ContextBudgetError("hermes_context_automatic_attempt_limit")
    if calls == 0 and api_kwargs.get("messages") != original["messages"]:
        raise ContextBudgetError("hermes_context_provider_messages_changed")
    if calls and getattr(agent, "_tp_managed_transit_context", None) is not None:
        feedback = getattr(agent, "_tp_managed_repair_feedback", "")
        expected = original["messages"] + [{"role": "user", "content": feedback}]
        # The pinned loop merges consecutive user messages before this final
        # boundary. Accept exactly that known transform, then restore the
        # canonical split wire so the full original JSON remains one value.
        merged = [original["messages"][0], {**original["messages"][1],
            "content": original["messages"][1]["content"] + "\n\n" + feedback}]
        if not feedback or api_kwargs.get("messages") not in (expected, merged):
            raise ContextBudgetError("hermes_context_repair_messages_changed")
        api_kwargs["messages"] = deepcopy(expected)
    actual = {**original, "messages": api_kwargs.get("messages"),
              "response_format": api_kwargs.get("response_format")}
    receipt = prepare_admission(actual)
    if not receipt or not receipt["admitted"]:
        raise ContextBudgetError((receipt or {}).get("reason", "hermes_context_not_admitted"))
    # This guard is immediately before transport, after middleware/overrides.
    # Failed admission is not counted as a dispatched provider attempt.
    agent._tp_context_model_calls = calls + 1
