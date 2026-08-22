"""Bounded, tool-free AstroFest design generation for the Hermes gateway."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import uuid
from typing import Any
from urllib.parse import urlsplit

try:
    from agent.durable_accounting import (
        JournalError,
        RequestConflictError,
        RequestInFlightError,
        RequestKeyError,
        RequestUnresolvedError,
        begin_request,
        complete_request,
        durable_agent_request_scope,
        fail_request,
        request_payload_sha256,
    )
    from agent.openrouter_accounting import (
        build_openrouter_accounting,
        openrouter_accounting_scope,
        record_openrouter_response,
        record_openrouter_unresolved_attempt,
    )
except ImportError:  # Local unit tests import the wrapper outside the image.
    from hermes_durable_accounting import (
        JournalError,
        RequestConflictError,
        RequestInFlightError,
        RequestKeyError,
        RequestUnresolvedError,
        begin_request,
        complete_request,
        durable_agent_request_scope,
        fail_request,
        request_payload_sha256,
    )
    from hermes_openrouter_accounting import (
        build_openrouter_accounting,
        openrouter_accounting_scope,
        record_openrouter_response,
        record_openrouter_unresolved_attempt,
    )


MAX_BODY_BYTES = 65_536
MAX_PROMPT_CHARS = 24_000
MAX_REFERENCES = 5
MAX_REFERENCE_CHARS = 2_048
MIN_OUTPUT_TOKENS = 64
DEFAULT_OUTPUT_TOKENS = 520
HARD_MAX_OUTPUT_TOKENS = 720
DEFAULT_TIMEOUT_SECONDS = 45.0
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

_SAFE_DESIGN_DEFAULTS: dict[str, Any] = {
    "preset": "editorial-light",
    "canvas": "#eaf1f6",
    "surface": "#ffffff",
    "surfaceAlt": "#f4f7f9",
    "ink": "#20233a",
    "muted": "#687385",
    "accent": "#b96950",
    "accentContrast": "#ffffff",
    "gold": "#c7a24a",
    "border": "#d8e3ea",
    "headingStyle": "editorial",
    "radius": "soft",
    "density": "balanced",
    "heroLayout": "split",
}

_SAFE_BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "entrance": "soft",
    "cardHover": "lift",
    "stickyNavigation": False,
}

_DESIGN_SCHEMA_TEXT = (
    '{"concept":string,"design":{"preset":"editorial-light|celestial-blue|solar-warm|cosmic-night",'
    '"canvas":"#RRGGBB","surface":"#RRGGBB","surfaceAlt":"#RRGGBB","ink":"#RRGGBB",'
    '"muted":"#RRGGBB","accent":"#RRGGBB","accentContrast":"#RRGGBB","gold":"#RRGGBB",'
    '"border":"#RRGGBB","headingStyle":"editorial|modern","radius":"crisp|soft|round",'
    '"density":"compact|balanced|airy","heroLayout":"split|centered"},'
    '"behavior":{"entrance":"none|soft|staggered","cardHover":"none|lift|glow",'
    '"stickyNavigation":boolean},"rationale":[string,string]}'
)

_SYSTEM_PROMPT = "\n".join(
    (
        "Ты арт-директор посадочной страницы AstroFest.",
        "Верни только один компактный JSON-объект без markdown, HTML, CSS и комментариев.",
        f"Обязательная схема: {_DESIGN_SCHEMA_TEXT}",
        "Сохраняй читаемость и достаточный контраст текста. Не добавляй разделы, изображения, скрипты или внешние зависимости.",
    )
)


class DesignRequestError(ValueError):
    def __init__(self, message: str, code: str = "invalid_design_request") -> None:
        super().__init__(message)
        self.code = code


def _error(message: str, code: str, *, accounting: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "code": code,
        }
    }
    if accounting is not None:
        payload["hermes_accounting"] = accounting
    return payload


def _bounded_timeout() -> float:
    try:
        configured = float(os.getenv("HERMES_DESIGN_TIMEOUT_SECONDS", "45"))
    except ValueError:
        configured = DEFAULT_TIMEOUT_SECONDS
    return min(60.0, max(5.0, configured))


def _https_reference(value: Any) -> str:
    if not isinstance(value, str):
        raise DesignRequestError("Reference URLs must be strings", "invalid_reference_url")
    value = value.strip()
    if not value or len(value) > MAX_REFERENCE_CHARS:
        raise DesignRequestError("Reference URL has an invalid length", "invalid_reference_url")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise DesignRequestError("Only public HTTPS reference URLs are accepted", "invalid_reference_url")
    return value


def validate_design_request(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise DesignRequestError("Request body must be a JSON object")
    allowed = {"prompt", "reference_urls", "max_tokens", "temperature"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise DesignRequestError(f"Unsupported fields: {', '.join(unknown)}", "unsupported_field")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise DesignRequestError("A non-empty prompt is required", "missing_prompt")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise DesignRequestError("Prompt is too long", "prompt_too_long")

    references = body.get("reference_urls", [])
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        raise DesignRequestError("At most five reference URLs are accepted", "invalid_reference_urls")
    normalized_references = [_https_reference(value) for value in references]

    max_tokens = body.get("max_tokens", DEFAULT_OUTPUT_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise DesignRequestError("max_tokens must be an integer", "invalid_max_tokens")
    max_tokens = min(HARD_MAX_OUTPUT_TOKENS, max(MIN_OUTPUT_TOKENS, max_tokens))

    temperature = body.get("temperature", 0.7)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise DesignRequestError("temperature must be a number", "invalid_temperature")
    temperature = float(temperature)
    if not 0.0 <= temperature <= 1.0:
        raise DesignRequestError("temperature must be between 0 and 1", "invalid_temperature")

    return {
        "prompt": prompt,
        "reference_urls": normalized_references,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def _required_enum(value: Any, allowed: set[str], field: str) -> None:
    if value not in allowed:
        raise DesignRequestError(f"Generated field {field} is invalid", "invalid_design_output")


def validate_generated_design(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"concept", "design", "behavior", "rationale"}:
        raise DesignRequestError("Generated design has an invalid root shape", "invalid_design_output")
    concept = value.get("concept")
    if not isinstance(concept, str) or not 3 <= len(concept.strip()) <= 120:
        raise DesignRequestError("Generated concept is invalid", "invalid_design_output")

    design = value.get("design")
    design_keys = {
        "preset", "canvas", "surface", "surfaceAlt", "ink", "muted", "accent",
        "accentContrast", "gold", "border", "headingStyle", "radius", "density", "heroLayout",
    }
    if not isinstance(design, dict) or set(design) != design_keys:
        raise DesignRequestError("Generated design tokens are invalid", "invalid_design_output")
    _required_enum(design["preset"], {"editorial-light", "celestial-blue", "solar-warm", "cosmic-night"}, "design.preset")
    for field in ("canvas", "surface", "surfaceAlt", "ink", "muted", "accent", "accentContrast", "gold", "border"):
        if not isinstance(design[field], str) or not _COLOR.fullmatch(design[field]):
            raise DesignRequestError(f"Generated color {field} is invalid", "invalid_design_output")
    _required_enum(design["headingStyle"], {"editorial", "modern"}, "design.headingStyle")
    _required_enum(design["radius"], {"crisp", "soft", "round"}, "design.radius")
    _required_enum(design["density"], {"compact", "balanced", "airy"}, "design.density")
    _required_enum(design["heroLayout"], {"split", "centered"}, "design.heroLayout")

    behavior = value.get("behavior")
    if not isinstance(behavior, dict) or set(behavior) != {"entrance", "cardHover", "stickyNavigation"}:
        raise DesignRequestError("Generated behavior is invalid", "invalid_design_output")
    _required_enum(behavior["entrance"], {"none", "soft", "staggered"}, "behavior.entrance")
    _required_enum(behavior["cardHover"], {"none", "lift", "glow"}, "behavior.cardHover")
    if not isinstance(behavior["stickyNavigation"], bool):
        raise DesignRequestError("Generated sticky navigation is invalid", "invalid_design_output")

    rationale = value.get("rationale")
    if not isinstance(rationale, list) or not 2 <= len(rationale) <= 5:
        raise DesignRequestError("Generated rationale is invalid", "invalid_design_output")
    if any(not isinstance(item, str) or not 3 <= len(item.strip()) <= 240 for item in rationale):
        raise DesignRequestError("Generated rationale item is invalid", "invalid_design_output")
    return value


def normalize_generated_design(value: Any) -> dict[str, Any]:
    """Convert a usable model object into the exact public design contract.

    JSON-mode providers still occasionally add harmless keys, omit a token, or
    invent an enum spelling. The runtime, rather than the browser, owns the
    safety boundary: valid model choices are retained and every unsupported
    value falls back to the reviewed AstroFest baseline.
    """
    if not isinstance(value, dict):
        raise DesignRequestError("Generated design is not an object", "invalid_design_output")

    concept_value = value.get("concept")
    concept = concept_value.strip()[:120] if isinstance(concept_value, str) else ""
    if len(concept) < 3:
        concept = "Безопасный вариант AstroFest"

    source_design = value.get("design") if isinstance(value.get("design"), dict) else {}
    design = dict(_SAFE_DESIGN_DEFAULTS)
    enum_fields = {
        "preset": {"editorial-light", "celestial-blue", "solar-warm", "cosmic-night"},
        "headingStyle": {"editorial", "modern"},
        "radius": {"crisp", "soft", "round"},
        "density": {"compact", "balanced", "airy"},
        "heroLayout": {"split", "centered"},
    }
    for field, allowed in enum_fields.items():
        if source_design.get(field) in allowed:
            design[field] = source_design[field]
    for field in ("canvas", "surface", "surfaceAlt", "ink", "muted", "accent", "accentContrast", "gold", "border"):
        candidate = source_design.get(field)
        if isinstance(candidate, str) and _COLOR.fullmatch(candidate):
            design[field] = candidate.lower()

    source_behavior = value.get("behavior") if isinstance(value.get("behavior"), dict) else {}
    behavior = dict(_SAFE_BEHAVIOR_DEFAULTS)
    if source_behavior.get("entrance") in {"none", "soft", "staggered"}:
        behavior["entrance"] = source_behavior["entrance"]
    if source_behavior.get("cardHover") in {"none", "lift", "glow"}:
        behavior["cardHover"] = source_behavior["cardHover"]
    if isinstance(source_behavior.get("stickyNavigation"), bool):
        behavior["stickyNavigation"] = source_behavior["stickyNavigation"]

    rationale_value = value.get("rationale")
    rationale = []
    if isinstance(rationale_value, list):
        rationale = [
            item.strip()[:240]
            for item in rationale_value
            if isinstance(item, str) and len(item.strip()) >= 3
        ][:5]
    for fallback in ("Сохранена читаемая структура фестиваля.", "Использована безопасная контрастная палитра."):
        if len(rationale) >= 2:
            break
        rationale.append(fallback)

    normalized = {
        "concept": concept,
        "design": design,
        "behavior": behavior,
        "rationale": rationale,
    }
    return validate_generated_design(normalized)


def _response_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        raise DesignRequestError("Provider returned no design", "empty_design_output")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise DesignRequestError("Provider returned no design", "empty_design_output")
    return content.strip()


def _execute_direct(adapter: Any, body: dict[str, Any], request_key: str) -> tuple[int, dict[str, Any], dict[str, Any]]:
    agent = adapter._create_agent()
    prompt = body["prompt"]
    if body["reference_urls"]:
        prompt += "\nHTTPS-референсы (используй как направление, не утверждай, что открывал их):\n- " + "\n- ".join(body["reference_urls"])

    dispatched = False
    with durable_agent_request_scope(agent, request_key), openrouter_accounting_scope(agent):
        try:
            client = agent._ensure_primary_openai_client(reason="astrofest_design_completion")
            dispatched = True
            response = client.chat.completions.create(
                model=agent.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=body["temperature"],
                max_tokens=body["max_tokens"],
                response_format={"type": "json_object"},
                extra_headers={"X-OpenRouter-Metadata": "enabled"},
                timeout=_bounded_timeout(),
            )
            record_openrouter_response(agent, response)
        except Exception:
            if dispatched:
                record_openrouter_unresolved_attempt(agent, "design_provider_attempt_failed", force=True)
            raise

        accounting = build_openrouter_accounting(
            agent,
            request_id="hermesacct_" + uuid.uuid4().hex,
            durable_request_key=request_key,
        )
        content = _response_content(response)
        try:
            design = normalize_generated_design(json.loads(content))
        except (json.JSONDecodeError, DesignRequestError):
            tokens = accounting["tokens"] if accounting else {}
            payload = _error(
                "The model response did not match the AstroFest design schema",
                "invalid_design_output",
                accounting=accounting,
            )
            payload["usage"] = {
                "prompt_tokens": tokens.get("input", 0),
                "completion_tokens": tokens.get("output", 0),
                "total_tokens": tokens.get("total", 0),
            }
            return 422, payload, {**payload["usage"], "_hermes_openrouter_accounting": accounting}

    tokens = accounting["tokens"] if accounting else {}
    usage = {
        "prompt_tokens": tokens.get("input", 0),
        "completion_tokens": tokens.get("output", 0),
        "total_tokens": tokens.get("total", 0),
    }
    payload = {
        "id": "designcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "model": getattr(agent, "model", ""),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps(design, ensure_ascii=False, separators=(",", ":"))},
            "finish_reason": "stop",
        }],
        "usage": usage,
        "hermes_accounting": accounting,
    }
    return 200, payload, {**usage, "_hermes_openrouter_accounting": accounting}


async def handle_design_completion(adapter: Any, request: Any, web: Any) -> Any:
    auth_error = adapter._check_auth(request)
    if auth_error:
        return auth_error
    content_length = getattr(request, "content_length", None)
    if content_length is not None and content_length > MAX_BODY_BYTES:
        return web.json_response(_error("Request body is too large", "request_too_large"), status=413)
    try:
        body = validate_design_request(await request.json())
    except DesignRequestError as exc:
        return web.json_response(_error(str(exc), exc.code), status=400)
    except Exception:
        return web.json_response(_error("Invalid JSON in request body", "invalid_json"), status=400)

    request_key = request.headers.get("Idempotency-Key")
    payload_sha256 = request_payload_sha256(body)
    try:
        decision = begin_request(request_key, payload_sha256)
    except RequestKeyError:
        return web.json_response(_error("A valid Idempotency-Key is required", "invalid_idempotency_key"), status=400)
    except RequestConflictError:
        return web.json_response(_error("Idempotency-Key payload conflict", "idempotency_payload_conflict"), status=409)
    except RequestInFlightError:
        return web.json_response(_error("Request is still in flight", "idempotency_in_flight"), status=409)
    except RequestUnresolvedError:
        return web.json_response(_error("Previous execution is unresolved", "idempotency_unresolved"), status=409)
    except (JournalError, sqlite3.Error, OSError):
        return web.json_response(_error("Accounting journal unavailable", "accounting_unavailable"), status=503)

    if decision["state"] == "completed":
        stored = decision["result"]
        return web.json_response(
            stored["payload"],
            status=stored["status"],
            headers={"X-Hermes-Idempotency-Replayed": "true"},
        )

    try:
        # The OpenAI client owns the hard per-request timeout. Cancelling a
        # worker thread from asyncio would not stop an in-flight provider call
        # and could permit a late billed completion after an HTTP timeout.
        status, payload, usage = await asyncio.to_thread(
            _execute_direct, adapter, body, request_key
        )
        complete_request(request_key, payload_sha256, {"status": status, "payload": payload}, usage)
        return web.json_response(payload, status=status)
    except Exception:
        try:
            fail_request(request_key, payload_sha256, "design_execution_failed")
        except (JournalError, sqlite3.Error, OSError):
            pass
        return web.json_response(_error("Design generation failed", "design_execution_failed"), status=502)
