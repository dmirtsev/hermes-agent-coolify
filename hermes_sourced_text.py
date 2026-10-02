"""Opt-in, bounded text extraction; never grants tools or shared memory."""


def strict_context_execution_mode(body):
    mode = body.get("tp_execution_mode")
    if mode is None:
        return None
    if mode not in ("sourced_text_v1", "interpretation_facts_v1"):
        raise ValueError("Unsupported tp_execution_mode")
    if body.get("tp_reading_context") is not None or body.get("stream"):
        raise ValueError("Sourced text requires an isolated non-streaming request")
    if body.get("tools") or body.get("tool_choice") not in (None, "none"):
        raise ValueError("Sourced text forbids tools")
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("Sourced text requires exactly system and user messages")
    for item, role in zip(messages, ("system", "user")):
        if not isinstance(item, dict) or item.get("role") != role:
            raise ValueError("Sourced text forbids conversation history")
        if not isinstance(item.get("content"), str) or not item["content"].strip():
            raise ValueError("Sourced text requires plain text")
    if sum(len(item["content"]) for item in messages) > 200000:
        raise ValueError("Sourced text exceeds 200000 characters")
    if mode == "interpretation_facts_v1" and sum(len(item["content"].encode("utf-8")) for item in messages) > 128 * 1024:
        raise ValueError("Interpretation facts exceed 128 KiB")
    return mode


def sourced_text_mode(body):
    """Compatibility predicate; only extraction opts into reduced reasoning."""
    return strict_context_execution_mode(body) == "sourced_text_v1"
