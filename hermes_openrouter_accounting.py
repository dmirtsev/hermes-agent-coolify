"""Authoritative OpenRouter generation accounting for the Hermes wrapper.

This module deliberately accepts only provider response objects and the
server-side agent configuration.  The cosmetic ``model`` supplied to the
OpenAI-compatible gateway request is never an input, so it cannot become
financial evidence by accident.
"""

from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

try:
    from agent.durable_accounting import (
        record_generation_evidence as _record_durable_generation,
        record_request_accounting as _record_durable_accounting,
        record_unresolved_evidence as _record_durable_unresolved,
        request_key_for_agent as _request_key_for_agent,
    )
except ImportError:  # Local unit tests import this module outside the image.
    from hermes_durable_accounting import (
        record_generation_evidence as _record_durable_generation,
        record_request_accounting as _record_durable_accounting,
        record_unresolved_evidence as _record_durable_unresolved,
        request_key_for_agent as _request_key_for_agent,
    )


MICRO_USD = Decimal("1000000")
_CURRENT_ACCOUNTING_AGENT: ContextVar[Any | None] = ContextVar(
    "hermes_openrouter_accounting_agent", default=None
)


class DurableAccountingWriteError(InterruptedError):
    """Abort the current Hermes attempt when durable evidence cannot be stored."""


def _durable_generation_or_abort(
    record: dict[str, Any], configured_model: str, request_key: str | None
) -> None:
    try:
        _record_durable_generation(
            record, configured_model, request_key=request_key
        )
    except Exception as exc:
        raise DurableAccountingWriteError("durable_accounting_write_failed") from exc


def _durable_unresolved_or_abort(
    reason: str, configured_model: str, request_key: str | None
) -> None:
    try:
        _record_durable_unresolved(
            reason, configured_model, request_key=request_key
        )
    except Exception as exc:
        raise DurableAccountingWriteError("durable_accounting_write_failed") from exc


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    result = getattr(value, name, default)
    if result is not default:
        return result
    extra = getattr(value, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get(name, default)
    return default


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _decimal_cost(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _micro_usd(value: Decimal) -> int:
    return int((value * MICRO_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _selected_endpoint(metadata: Any) -> Any:
    endpoints = _field(metadata, "endpoints", {})
    available = _field(endpoints, "available", [])
    if not isinstance(available, (list, tuple)):
        return None
    for endpoint in available:
        if _field(endpoint, "selected", False) is True:
            return endpoint
    return None


def extract_openrouter_generation(response: Any) -> dict[str, Any]:
    """Extract only fields reported by OpenRouter/the executed provider."""
    usage = _field(response, "usage")
    prompt_details = _field(usage, "prompt_tokens_details", {})
    completion_details = _field(usage, "completion_tokens_details", {})
    metadata = _field(response, "openrouter_metadata", {})
    endpoint = _selected_endpoint(metadata)

    generation_id = _non_empty_text(_field(response, "id"))
    if generation_id and (
        generation_id.startswith("stream-hermes-")
        or generation_id == "partial-stream-stub"
    ):
        generation_id = None

    executed_model = _non_empty_text(_field(response, "model"))
    if executed_model is None:
        executed_model = _non_empty_text(_field(endpoint, "model"))

    executed_provider = _non_empty_text(_field(endpoint, "provider"))
    if executed_provider is None:
        executed_provider = _non_empty_text(_field(response, "provider"))
    if executed_provider is None:
        executed_provider = _non_empty_text(_field(metadata, "provider"))

    input_tokens = _non_negative_int(_field(usage, "prompt_tokens"))
    output_tokens = _non_negative_int(_field(usage, "completion_tokens"))
    total_tokens = _non_negative_int(_field(usage, "total_tokens"))
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens

    cost = _decimal_cost(_field(usage, "cost"))
    return {
        "generation_id": generation_id,
        "executed_provider": executed_provider,
        "executed_model": executed_model,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": _non_negative_int(_field(prompt_details, "cached_tokens")),
            "cache_write": _non_negative_int(_field(prompt_details, "cache_write_tokens")),
            "reasoning": _non_negative_int(_field(completion_details, "reasoning_tokens")),
            "total": total_tokens,
        },
        "_actual_cost": cost,
    }


def _is_openrouter_agent(agent: Any) -> bool:
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    if provider == "openrouter":
        return True
    base_url = str(getattr(agent, "base_url", "") or "").strip().lower()
    return "openrouter.ai" in base_url


def _has_openrouter_accounting(agent: Any) -> bool:
    """Return whether this request has ever dispatched through OpenRouter.

    Hermes mutates ``agent.provider`` during provider fallback.  Financial
    evidence collected before that mutation must remain visible.
    """
    return bool(
        _is_openrouter_agent(agent)
        or getattr(agent, "_openrouter_accounting_configured_provider", None) == "openrouter"
        or isinstance(getattr(agent, "_openrouter_accounting_generations", None), list)
    )


@contextmanager
def openrouter_accounting_scope(agent: Any):
    """Associate direct auxiliary calls with the current gateway request."""
    token = _CURRENT_ACCOUNTING_AGENT.set(agent)
    try:
        yield
    finally:
        _CURRENT_ACCOUNTING_AGENT.reset(token)


def record_current_openrouter_response(response: Any) -> bool:
    agent = _CURRENT_ACCOUNTING_AGENT.get()
    return bool(agent is not None and record_openrouter_response(agent, response))


def record_current_openrouter_unresolved_attempt(reason: str) -> bool:
    agent = _CURRENT_ACCOUNTING_AGENT.get()
    return bool(
        agent is not None
        and _has_openrouter_accounting(agent)
        and record_openrouter_unresolved_attempt(agent, reason, force=True)
    )


def record_openrouter_response(agent: Any, response: Any) -> bool:
    """Record one returned OpenRouter generation, idempotently by id.

    Call this once for every provider response, before Hermes decides whether
    that response is usable or retryable.  A retry response can itself be
    billable and therefore must not disappear from the accounting trail.
    """
    if not _is_openrouter_agent(agent):
        return False

    if response is None:
        return record_openrouter_unresolved_attempt(agent, "empty_provider_response")

    request_key = _request_key_for_agent(agent)
    record = extract_openrouter_generation(response)
    records = getattr(agent, "_openrouter_accounting_generations", None)
    if not isinstance(records, list):
        records = []
        setattr(agent, "_openrouter_accounting_generations", records)
        setattr(agent, "_openrouter_accounting_configured_provider", "openrouter")
        setattr(
            agent,
            "_openrouter_accounting_configured_model",
            str(getattr(agent, "model", "") or "").strip(),
        )

    generation_id = record["generation_id"]
    if generation_id:
        for existing in records:
            if existing.get("generation_id") != generation_id:
                continue
            # A later delivery may contain the final usage chunk.  Fill only
            # missing evidence and replace zero token buckets with non-zero
            # provider values; never count the same generation twice.
            for key in ("executed_provider", "executed_model"):
                if not existing.get(key) and record.get(key):
                    existing[key] = record[key]
            if existing.get("_actual_cost") is None and record.get("_actual_cost") is not None:
                existing["_actual_cost"] = record["_actual_cost"]
            elif (
                existing.get("_actual_cost") is not None
                and record.get("_actual_cost") is not None
                and existing["_actual_cost"] != record["_actual_cost"]
            ):
                existing["_evidence_conflict"] = True
            for key in ("executed_provider", "executed_model"):
                if existing.get(key) and record.get(key) and existing[key] != record[key]:
                    existing["_evidence_conflict"] = True
            if (
                sum(existing.get("tokens", {}).values()) > 0
                and sum(record["tokens"].values()) > 0
                and existing["tokens"] != record["tokens"]
            ):
                existing["_evidence_conflict"] = True
            if sum(existing.get("tokens", {}).values()) == 0 and sum(record["tokens"].values()) > 0:
                existing["tokens"] = record["tokens"]
            _durable_generation_or_abort(
                existing,
                str(getattr(agent, "model", "") or "").strip(),
                request_key,
            )
            return False

    records.append(record)
    _durable_generation_or_abort(
        record,
        str(getattr(agent, "model", "") or "").strip(),
        request_key,
    )
    return True


def record_openrouter_unresolved_attempt(
    agent: Any, reason: str, *, force: bool = False
) -> bool:
    """Mark a dispatched attempt whose generation evidence never arrived."""
    if not _is_openrouter_agent(agent) and not (force and _has_openrouter_accounting(agent)):
        return False
    request_key = _request_key_for_agent(agent)
    records = getattr(agent, "_openrouter_accounting_generations", None)
    if not isinstance(records, list):
        records = []
        setattr(agent, "_openrouter_accounting_generations", records)
        setattr(agent, "_openrouter_accounting_configured_provider", "openrouter")
        setattr(
            agent,
            "_openrouter_accounting_configured_model",
            str(getattr(agent, "model", "") or "").strip(),
        )
    unresolved = {
        "generation_id": None,
        "executed_provider": None,
        "executed_model": None,
        "tokens": {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "reasoning": 0,
            "total": 0,
        },
        "_actual_cost": None,
        "_unresolved_reason": str(reason or "provider_attempt_failed"),
    }
    records.append(unresolved)
    _durable_unresolved_or_abort(
        unresolved["_unresolved_reason"],
        str(getattr(agent, "model", "") or "").strip(),
        request_key,
    )
    return True


def _public_generation(record: dict[str, Any], sequence: int) -> dict[str, Any]:
    generation_id = record.get("generation_id")
    cost = record.get("_actual_cost")
    if record.get("_evidence_conflict"):
        cost_payload = {
            "status": "cost_unavailable",
            "reason": "duplicate_generation_conflict",
            "fully_reconciled": False,
        }
    elif isinstance(cost, Decimal) and generation_id:
        cost_payload = {
            "status": "actual",
            "amount_micro_usd": _micro_usd(cost),
            "amount_usd": _decimal_string(cost),
            "source": "openrouter_usage",
            "fully_reconciled": True,
        }
    elif generation_id:
        cost_payload = {
            "status": "pending",
            "reason": "generation_lookup_pending",
            "fully_reconciled": False,
        }
    else:
        cost_payload = {
            "status": "cost_unavailable",
            "reason": "upstream_generation_id_missing",
            "fully_reconciled": False,
        }
    return {
        "sequence": sequence,
        "generation_id": generation_id,
        "executed_provider": record.get("executed_provider"),
        "executed_model": record.get("executed_model"),
        "tokens": copy.deepcopy(record["tokens"]),
        "cost": cost_payload,
    }


def build_openrouter_accounting(
    agent: Any,
    request_id: str,
    *,
    durable_request_key: str | None = None,
) -> dict[str, Any] | None:
    """Build the public aggregate without using catalog estimates."""
    if not _has_openrouter_accounting(agent):
        return None

    private_records = getattr(agent, "_openrouter_accounting_generations", None)
    if not isinstance(private_records, list):
        private_records = []
    generations = [
        _public_generation(record, index)
        for index, record in enumerate(private_records, start=1)
    ]

    token_names = ("input", "output", "cache_read", "cache_write", "reasoning", "total")
    tokens = {
        name: sum(generation["tokens"][name] for generation in generations)
        for name in token_names
    }
    generation_ids = [
        generation["generation_id"]
        for generation in generations
        if generation["generation_id"]
    ]
    provider_models = list(dict.fromkeys(
        generation["executed_model"]
        for generation in generations
        if generation["executed_model"]
    ))
    executed_providers = list(dict.fromkeys(
        generation["executed_provider"]
        for generation in generations
        if generation["executed_provider"]
    ))

    actual_records = [
        record
        for record in private_records
        if (
            record.get("generation_id")
            and isinstance(record.get("_actual_cost"), Decimal)
            and not record.get("_evidence_conflict")
        )
    ]
    if any(record.get("_evidence_conflict") for record in private_records):
        cost = {
            "status": "cost_unavailable",
            "reason": "duplicate_generation_conflict",
            "generation_ids": generation_ids,
            "fully_reconciled": False,
        }
    elif generations and len(actual_records) == len(generations):
        amount = sum((record["_actual_cost"] for record in actual_records), Decimal("0"))
        cost = {
            "status": "actual",
            # The canonical integer debit is rounded once from the exact
            # request total.  Per-generation display rounding is not additive.
            "amount_micro_usd": _micro_usd(amount),
            "amount_usd": _decimal_string(amount),
            "currency": "USD",
            "source": "openrouter_usage",
            "fully_reconciled": True,
        }
    elif generations and all(generation["generation_id"] for generation in generations):
        cost = {
            "status": "pending",
            "reason": "generation_lookup_pending",
            "generation_ids": generation_ids,
            "fully_reconciled": False,
        }
    else:
        cost = {
            "status": "cost_unavailable",
            "reason": (
                "upstream_generation_id_missing"
                if generations
                else "no_billable_generations"
            ),
            "generation_ids": generation_ids,
            "fully_reconciled": False,
        }

    configured_model = str(
        getattr(agent, "_openrouter_accounting_configured_model", "")
        or getattr(agent, "model", "")
        or ""
    ).strip()
    result = {
        "schema_version": 2,
        "request_id": str(request_id),
        "runtime": {
            "tier": os.getenv("HERMES_RUNTIME_TIER", "unknown").strip().lower(),
            "runtime_id": os.getenv("HERMES_RUNTIME_ID", "unknown").strip(),
        },
        "model_execution": {
            "configured_provider": "openrouter",
            "configured_model": configured_model,
            "identity_status": "provider_reported" if provider_models else "configured_only",
            "executed_providers": executed_providers,
            "provider_reported_models": provider_models,
            "upstream_generation_ids": generation_ids,
        },
        "generations": generations,
        "tokens": tokens,
        "cost": cost,
    }
    request_key = durable_request_key or _request_key_for_agent(agent)
    _record_durable_accounting(result, request_key=request_key)
    return result


def accounting_for_request(value: Any, request_id: str) -> dict[str, Any] | None:
    """Clone an aggregate while preserving its stable billing event id.

    The internal id is generated inside ``_run_agent`` and therefore survives
    an Idempotency-Key cache replay.  Legacy/test payloads using ``internal``
    still receive the public response id as a compatibility fallback.
    """
    if not isinstance(value, dict):
        return None
    result = copy.deepcopy(value)
    if not result.get("request_id") or result.get("request_id") == "internal":
        result["request_id"] = str(request_id)
    return result
