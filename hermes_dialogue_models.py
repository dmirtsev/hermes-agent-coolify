"""Backend-owned, opt-in model profiles for billed Cabinet dialogues.

The incoming cosmetic model never selects a provider. Only these public codes
can select a profile, on the OpenRouter economy runtime. No shared config or
process-global model is changed; every concurrent request creates its own agent.
"""
from __future__ import annotations

import os
import json
from copy import deepcopy
from hermes_managed_validation import _rows, ManagedValidationContextError

PROFILES = {
    "deepseek_flash": {
        "model": "deepseek/deepseek-v4-flash",
        "providers": ["deepinfra/fp8", "gmicloud/fp8"],
        "max_price": {"prompt": 0.5, "completion": 1.5},
    },
    "gpt_luna": {"model": "openai/gpt-6-luna", "provider": "openai", "reasoning_effort": "high"},
    "gpt_terra": {"model": "openai/gpt-6.1-terra", "provider": "openai", "reasoning_effort": "medium"},
}


class DialogueModelError(ValueError):
    pass


def choice_enabled(env=None):
    env = os.environ if env is None else env
    return (env.get("HERMES_DIALOGUE_MODEL_CHOICE_ENABLED") == "true"
            and env.get("HERMES_RUNTIME_TIER") == "economy"
            and env.get("HERMES_FIXED_MODEL_PROVIDER") == "openrouter")


def terra_enabled(env=None):
    env = os.environ if env is None else env
    return (choice_enabled(env) and env.get("DEPLOYMENT_ENVIRONMENT") == "test"
            and env.get("HERMES_TERRA_PERIODS_ENABLED") == "true")


def _verified_transit_moment(body):
    """Recognise the existing saved-composition moment wire shape.

    Moment readings do not carry a versioned astrology-method envelope. They
    are authorised by the packed temporal marker plus the saved natal and
    transit layers in the same user message.
    """
    try:
        message = body.get("messages", [])[1]
        payload = json.loads(message["content"])
        context = payload.get("context") or {}
        temporal = context.get("temporal") or {}
        if temporal.get("kind") != "transit_moment":
            return False
        source = context.get("source")
        temporal_source = temporal.get("source")
        natal_source = payload.get("natalSource")
        facts = payload.get("facts") or {}
        layers = facts.get("layers")
        statements = facts.get("statements")
        if (not isinstance(source, dict) or not source.get("resultId") or
                not isinstance(natal_source, dict) or not natal_source.get("resultId") or
                source.get("resultId") != natal_source.get("resultId")):
            return False
        if (not isinstance(temporal_source, dict) or
                temporal_source.get("natalResultId") != natal_source.get("resultId")):
            return False
        if not isinstance(layers, list) or not layers or not _rows(statements):
            return False
        if any(not isinstance(layer, dict) for layer in layers):
            return False
        kinds = {layer.get("chartType") for layer in layers}
        return kinds == {"natal", "transit"} and all(
            layer.get("relationMethod") == "transit"
            for layer in layers if layer.get("chartType") == "transit")
    except (IndexError, KeyError, TypeError, AttributeError, ValueError, ManagedValidationContextError):
        return False


def validate_dialogue_model(body, idempotency_key, env=None):
    code = body.get("tp_dialogue_model")
    if code is None:
        return None
    if not isinstance(code, str) or code not in PROFILES:
        raise DialogueModelError("dialogue_model_invalid")
    if not choice_enabled(env):
        raise DialogueModelError("dialogue_model_choice_unavailable")
    if code == "gpt_terra" and (not terra_enabled(env) or
            (body.get("tp_context_budget") or {}).get("profile") != "natal_transit" or
            body.get("tp_dialogue_mode") == "free_dialogue" or
            (not body.get("tp_managed_method_context") and
             (body.get("tp_execution_mode") != "interpretation_facts_v1" or
              body.get("tp_answer_format") != "hermes_managed_v1" or
              not _verified_transit_moment(body)))):
        raise DialogueModelError("dialogue_model_terra_period_only")
    if body.get("stream") or not idempotency_key:
        raise DialogueModelError("dialogue_model_requires_durable_non_streaming")
    if body.get("model") != PROFILES[code]["model"]:
        raise DialogueModelError("dialogue_model_identity_mismatch")
    return code


def dialogue_agent_settings(code, runtime_kwargs, model, reasoning_config, fallback_model):
    if code is None:
        return runtime_kwargs, model, reasoning_config, fallback_model
    if code not in PROFILES or not choice_enabled():
        raise DialogueModelError("dialogue_model_choice_unavailable")
    if code == "gpt_terra" and not terra_enabled():
        raise DialogueModelError("dialogue_model_terra_unavailable")
    if (runtime_kwargs.get("provider") != "openrouter" or
            runtime_kwargs.get("base_url", "").rstrip("/") != "https://openrouter.ai/api/v1"):
        raise DialogueModelError("dialogue_model_runtime_mismatch")
    profile = PROFILES[code]
    kwargs = dict(runtime_kwargs)
    # Credential pools may contain locks; copy only the JSON routing settings.
    overrides = deepcopy(runtime_kwargs.get("request_overrides") or {})
    kwargs["request_overrides"] = overrides
    # Per-request provider identity is pinned. Remove inherited model fallbacks
    # and routing choices, while retaining other backend request configuration.
    for key in ("model", "models", "route", "reasoning", "max_tokens"):
        overrides.pop(key, None)
    extra = overrides.setdefault("extra_body", {})
    for key in ("models", "route", "reasoning"):
        extra.pop(key, None)
    if code == "deepseek_flash":
        # OpenRouter may fail over only between the two approved endpoints for
        # this exact model; no other model or provider is admissible.
        extra["provider"] = {
            "order": list(profile["providers"]),
            "only": list(profile["providers"]),
            "allow_fallbacks": True,
            "max_price": dict(profile["max_price"]),
        }
        # The outer conversation loop owns retries for this route.  Keep the
        # SDK from multiplying a provider failure inside each attempt.
    else:
        extra["provider"] = {"only": ["openai"], "allow_fallbacks": False}
        if code == "gpt_terra":
            extra["provider"].update(require_parameters=True,
                                     max_price={"prompt": 2.5, "completion": 7.5})
    extra["service_tier"] = "default"
    extra["reasoning"] = {"effort": profile.get("reasoning_effort", "high")}
    kwargs["max_tokens"] = 8192
    return kwargs, profile["model"], {"effort": profile.get("reasoning_effort", "high")}, None


def public_dialogue_models():
    return {"version": 1, "codes": [code for code in PROFILES
            if code != "gpt_terra" or terra_enabled()]} if choice_enabled() else None
