"""Backend-owned, opt-in model profiles for billed Cabinet dialogues.

The incoming cosmetic model never selects a provider. Only these public codes
can select a profile, on the OpenRouter economy runtime. No shared config or
process-global model is changed; every concurrent request creates its own agent.
"""
from __future__ import annotations

import os
from copy import deepcopy

PROFILES = {
    "deepseek_flash": {"model": "deepseek/deepseek-v4-flash", "provider": "novita/fp8"},
    "gpt_luna": {"model": "openai/gpt-6-luna", "provider": "openai"},
}


class DialogueModelError(ValueError):
    pass


def choice_enabled(env=None):
    env = os.environ if env is None else env
    return (env.get("HERMES_DIALOGUE_MODEL_CHOICE_ENABLED") == "true"
            and env.get("HERMES_RUNTIME_TIER") == "economy"
            and env.get("HERMES_FIXED_MODEL_PROVIDER") == "openrouter")


def validate_dialogue_model(body, idempotency_key, env=None):
    code = body.get("tp_dialogue_model")
    if code is None:
        return None
    if not isinstance(code, str) or code not in PROFILES:
        raise DialogueModelError("dialogue_model_invalid")
    if not choice_enabled(env):
        raise DialogueModelError("dialogue_model_choice_unavailable")
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
    extra["provider"] = {"only": [profile["provider"]], "allow_fallbacks": False}
    extra["service_tier"] = "default"
    kwargs["max_tokens"] = 8192
    return kwargs, profile["model"], {"effort": "high"}, None


def public_dialogue_models():
    return {"version": 1, "codes": list(PROFILES)} if choice_enabled() else None
