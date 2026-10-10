"""Trusted, request-scoped system prompt replacement for Cabinet dialogues."""
import hashlib
import re
from copy import deepcopy


def validate_behavior_policy(body, idempotency_key):
    policy = body.get("tp_hermes_policy")
    if policy is None:
        if body.get("tp_answer_format") == "hermes_managed_v1":
            raise ValueError("hermes_policy_required")
        # Legacy planner/extraction bodies already carry response_format.
        # Their existing transport stays outside managed-policy forwarding.
        return None
    if "response_format" in body and body["response_format"] != {"type": "json_object"}:
        raise ValueError("hermes_policy_invalid_response_format")
    if (not isinstance(policy, dict) or set(policy) !=
            {"version_id", "version", "sha256", "system_sha256"}):
        raise ValueError("hermes_policy_invalid")
    if (not isinstance(policy["version_id"], str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", policy["version_id"]) or
            type(policy["version"]) is not int or policy["version"] < 1):
        raise ValueError("hermes_policy_invalid")
    if any(not isinstance(policy[key], str) or not re.fullmatch(r"[a-f0-9]{64}", policy[key])
           for key in ("sha256", "system_sha256")):
        raise ValueError("hermes_policy_invalid")
    if (not idempotency_key or body.get("stream") or
            body.get("tp_execution_mode") != "interpretation_facts_v1" or
            body.get("tp_answer_format") != "hermes_managed_v1" or
            body.get("tp_reading_context") is not None or body.get("tools") or
            body.get("tool_choice") not in (None, "none")):
        raise ValueError("hermes_policy_requires_isolated_durable_dialogue")
    messages = body.get("messages")
    if (not isinstance(messages, list) or len(messages) != 2 or
            any(not isinstance(m, dict) or m.get("role") != role or
                not isinstance(m.get("content"), str) or not m["content"].strip()
                for m, role in zip(messages, ("system", "user")))):
        raise ValueError("hermes_policy_invalid_messages")
    actual = hashlib.sha256(messages[0]["content"].encode("utf-8")).hexdigest()
    if actual != policy["system_sha256"]:
        raise ValueError("hermes_policy_system_hash_mismatch")
    return dict(policy)


def managed_response_settings(runtime_kwargs, response_format):
    """Set output format before constructing this request's provider agent."""
    if response_format is None:
        return runtime_kwargs
    if response_format != {"type": "json_object"}:
        raise ValueError("hermes_policy_invalid_response_format")
    kwargs = dict(runtime_kwargs)
    overrides = deepcopy(runtime_kwargs.get("request_overrides") or {})
    overrides["response_format"] = {"type": "json_object"}
    kwargs["request_overrides"] = overrides
    return kwargs


def install_managed_system_prompt(agent, prompt):
    if prompt is None:
        return
    agent._tp_managed_system_prompt = prompt
    agent._cached_system_prompt = prompt
    # Replacement is already the complete provider system instruction.
    agent.ephemeral_system_prompt = None
