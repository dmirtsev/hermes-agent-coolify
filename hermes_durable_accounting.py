"""Durable, fail-closed accounting journal for billed Hermes requests.

The gateway owns request idempotency while the OpenRouter accounting module
owns provider evidence.  This module is the small persistent seam between
them.  It deliberately stores no API keys and never derives a financial cost
from catalog prices or token estimates.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterator


SCHEMA_VERSION = 1
MICRO_USD = Decimal("1000000")
DEFAULT_JOURNAL_PATH = "/opt/data/hermes-accounting.sqlite3"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_REQUEST_KEY_LENGTH = 160

_CURRENT_REQUEST_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hermes_durable_accounting_request_key", default=None
)


class JournalError(RuntimeError):
    pass


class RequestKeyError(JournalError):
    pass


class RequestConflictError(JournalError):
    pass


class RequestInFlightError(JournalError):
    pass


class RequestUnresolvedError(JournalError):
    pass


class RequestNotFoundError(JournalError):
    pass


class LookupError(JournalError):
    def __init__(self, code: str, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _now() -> float:
    return time.time()


def _journal_path() -> Path:
    raw = os.getenv("HERMES_ACCOUNTING_JOURNAL_PATH", DEFAULT_JOURNAL_PATH).strip()
    if not raw:
        raise JournalError("accounting_journal_path_missing")
    return Path(raw)


def normalize_request_key(value: Any) -> str:
    if not isinstance(value, str):
        raise RequestKeyError("idempotency_key_required")
    value = value.strip()
    if not value or len(value) > MAX_REQUEST_KEY_LENGTH:
        raise RequestKeyError("invalid_idempotency_key")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise RequestKeyError("invalid_idempotency_key")
    return value


def request_payload_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return vars(value)
    return str(value)


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _load(value: str | None) -> Any:
    return json.loads(value) if value else None


def _connect() -> sqlite3.Connection:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounting_request (
          request_key TEXT PRIMARY KEY,
          payload_sha256 TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN ('in_flight', 'completed', 'failed')),
          configured_provider TEXT,
          configured_model TEXT,
          runtime_tier TEXT,
          runtime_id TEXT,
          result_json TEXT,
          usage_json TEXT,
          accounting_json TEXT,
          failure_code TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS accounting_evidence_event (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          request_key TEXT NOT NULL REFERENCES accounting_request(request_key) ON DELETE RESTRICT,
          kind TEXT NOT NULL CHECK (kind IN ('generation', 'unresolved')),
          generation_id TEXT,
          evidence_json TEXT NOT NULL,
          created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS accounting_evidence_request_idx
          ON accounting_evidence_event(request_key, id);
        CREATE INDEX IF NOT EXISTS accounting_evidence_generation_idx
          ON accounting_evidence_event(generation_id);

        CREATE TABLE IF NOT EXISTS accounting_reconciliation_event (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          request_key TEXT NOT NULL REFERENCES accounting_request(request_key) ON DELETE RESTRICT,
          generation_id TEXT,
          outcome TEXT NOT NULL,
          http_status INTEGER,
          error_code TEXT,
          created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS accounting_reconcile_request_idx
          ON accounting_reconciliation_event(request_key, id);
        """
    )
    return connection


def ensure_journal() -> Path:
    with contextlib.closing(_connect()):
        pass
    return _journal_path()


@contextlib.contextmanager
def durable_request_scope(request_key: str | None) -> Iterator[None]:
    token = _CURRENT_REQUEST_KEY.set(
        normalize_request_key(request_key) if request_key is not None else None
    )
    try:
        yield
    finally:
        _CURRENT_REQUEST_KEY.reset(token)


def current_request_key() -> str | None:
    return _CURRENT_REQUEST_KEY.get()


def begin_request(request_key: str, payload_sha256: str) -> dict[str, Any]:
    request_key = normalize_request_key(request_key)
    if not isinstance(payload_sha256, str) or len(payload_sha256) != 64:
        raise JournalError("invalid_payload_sha256")
    now = _now()
    with contextlib.closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM accounting_request WHERE request_key = ?", (request_key,)
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO accounting_request (
                  request_key, payload_sha256, state, created_at, updated_at
                ) VALUES (?, ?, 'in_flight', ?, ?)
                """,
                (request_key, payload_sha256, now, now),
            )
            connection.commit()
            return {"state": "claimed", "requestKey": request_key}
        if row["payload_sha256"] != payload_sha256:
            connection.rollback()
            raise RequestConflictError("idempotency_payload_conflict")
        state = row["state"]
        if state == "completed":
            result = _load(row["result_json"])
            usage = _load(row["usage_json"])
            if not isinstance(result, dict) or not isinstance(usage, dict):
                connection.rollback()
                raise RequestUnresolvedError("completed_result_unreadable")
            connection.commit()
            return {
                "state": "completed",
                "requestKey": request_key,
                "result": result,
                "usage": usage,
            }
        connection.rollback()
        if state == "failed":
            raise RequestUnresolvedError(row["failure_code"] or "request_unresolved")
        raise RequestInFlightError("request_in_flight")


def record_request_accounting(accounting: Any) -> bool:
    request_key = current_request_key()
    if request_key is None or not isinstance(accounting, dict):
        return False
    with contextlib.closing(_connect()) as connection:
        cursor = connection.execute(
            """
            UPDATE accounting_request
            SET accounting_json = ?, updated_at = ?
            WHERE request_key = ?
            """,
            (_dump(accounting), _now(), request_key),
        )
        return cursor.rowcount == 1


def complete_request(
    request_key: str,
    payload_sha256: str,
    result: dict[str, Any],
    usage: dict[str, Any],
) -> None:
    request_key = normalize_request_key(request_key)
    accounting = usage.get("_hermes_openrouter_accounting")
    with contextlib.closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT payload_sha256, state, result_json, usage_json
            FROM accounting_request WHERE request_key = ?
            """,
            (request_key,),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise RequestNotFoundError("request_not_found")
        if row["payload_sha256"] != payload_sha256:
            connection.rollback()
            raise RequestConflictError("idempotency_payload_conflict")
        serialized_result = _dump(result)
        serialized_usage = _dump(usage)
        if row["state"] == "completed":
            if (
                row["result_json"] == serialized_result
                and row["usage_json"] == serialized_usage
            ):
                connection.commit()
                return
            connection.rollback()
            raise RequestConflictError("completed_result_conflict")
        if row["state"] != "in_flight":
            connection.rollback()
            raise RequestUnresolvedError("request_unresolved")
        now = _now()
        connection.execute(
            """
            UPDATE accounting_request
            SET state = 'completed', result_json = ?, usage_json = ?,
                accounting_json = COALESCE(?, accounting_json), failure_code = NULL,
                completed_at = COALESCE(completed_at, ?), updated_at = ?
            WHERE request_key = ?
            """,
            (
                serialized_result,
                serialized_usage,
                _dump(accounting) if isinstance(accounting, dict) else None,
                now,
                now,
                request_key,
            ),
        )
        connection.commit()


def fail_request(request_key: str, payload_sha256: str, reason: str) -> None:
    request_key = normalize_request_key(request_key)
    safe_reason = str(reason or "request_failed")[:160]
    with contextlib.closing(_connect()) as connection:
        connection.execute(
            """
            UPDATE accounting_request
            SET state = 'failed', failure_code = ?, updated_at = ?
            WHERE request_key = ? AND payload_sha256 = ? AND state != 'completed'
            """,
            (safe_reason, _now(), request_key, payload_sha256),
        )


def record_generation_evidence(record: dict[str, Any], configured_model: str) -> bool:
    request_key = current_request_key()
    if request_key is None:
        return False
    generation_id = record.get("generation_id")
    payload = copy.deepcopy(record)
    payload["configured_model"] = str(configured_model or "").strip()
    with contextlib.closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE accounting_request
            SET configured_provider = 'openrouter', configured_model = ?,
                runtime_tier = ?, runtime_id = ?, updated_at = ?
            WHERE request_key = ?
            """,
            (
                payload["configured_model"],
                os.getenv("HERMES_RUNTIME_TIER", "unknown").strip().lower(),
                os.getenv("HERMES_RUNTIME_ID", "unknown").strip(),
                _now(),
                request_key,
            ),
        )
        connection.execute(
            """
            INSERT INTO accounting_evidence_event (
              request_key, kind, generation_id, evidence_json, created_at
            ) VALUES (?, 'generation', ?, ?, ?)
            """,
            (
                request_key,
                str(generation_id).strip() if generation_id else None,
                _dump(payload),
                _now(),
            ),
        )
        connection.commit()
    return True


def record_unresolved_evidence(reason: str, configured_model: str) -> bool:
    request_key = current_request_key()
    if request_key is None:
        return False
    payload = {
        "reason": str(reason or "provider_attempt_failed")[:160],
        "configured_model": str(configured_model or "").strip(),
    }
    with contextlib.closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE accounting_request
            SET configured_provider = 'openrouter', configured_model = ?,
                runtime_tier = ?, runtime_id = ?, updated_at = ?
            WHERE request_key = ?
            """,
            (
                payload["configured_model"],
                os.getenv("HERMES_RUNTIME_TIER", "unknown").strip().lower(),
                os.getenv("HERMES_RUNTIME_ID", "unknown").strip(),
                _now(),
                request_key,
            ),
        )
        connection.execute(
            """
            INSERT INTO accounting_evidence_event (
              request_key, kind, generation_id, evidence_json, created_at
            ) VALUES (?, 'unresolved', NULL, ?, ?)
            """,
            (request_key, _dump(payload), _now()),
        )
        connection.commit()
    return True


def _decimal_cost(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _micro_usd(value: Decimal) -> int:
    return int((value * MICRO_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _zero_tokens() -> dict[str, int]:
    return {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning": 0,
        "total": 0,
    }


def _merge_generation(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(previous)
    for field in ("executed_provider", "executed_model"):
        if current.get(field):
            if merged.get(field) and merged[field] != current[field]:
                merged["_evidence_conflict"] = True
            else:
                merged[field] = current[field]
    current_cost = _decimal_cost(current.get("_actual_cost"))
    previous_cost = _decimal_cost(merged.get("_actual_cost"))
    if current_cost is not None:
        if previous_cost is not None and previous_cost != current_cost:
            merged["_evidence_conflict"] = True
        else:
            merged["_actual_cost"] = _decimal_string(current_cost)
    current_tokens = current.get("tokens")
    if isinstance(current_tokens, dict) and sum(current_tokens.values()) > 0:
        previous_tokens = merged.get("tokens") or _zero_tokens()
        if sum(previous_tokens.values()) > 0 and previous_tokens != current_tokens:
            merged["_evidence_conflict"] = True
        else:
            merged["tokens"] = current_tokens
    if current.get("_evidence_conflict"):
        merged["_evidence_conflict"] = True
    return merged


def _request_row_and_events(request_key: str) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    with contextlib.closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM accounting_request WHERE request_key = ?", (request_key,)
        ).fetchone()
        if row is None:
            raise RequestNotFoundError("request_not_found")
        events = connection.execute(
            """
            SELECT * FROM accounting_evidence_event
            WHERE request_key = ? ORDER BY id ASC
            """,
            (request_key,),
        ).fetchall()
        return row, events


def _accounting_from_events(request_key: str, row: sqlite3.Row, events: list[sqlite3.Row]) -> dict[str, Any] | None:
    generations_by_id: dict[str, dict[str, Any]] = {}
    ordered: list[tuple[str, dict[str, Any]]] = []
    unresolved_count = 0
    for event in events:
        payload = _load(event["evidence_json"])
        if event["kind"] == "unresolved":
            unresolved_count += 1
            ordered.append(
                (
                    f"unresolved:{event['id']}",
                    {
                        "generation_id": None,
                        "executed_provider": None,
                        "executed_model": None,
                        "tokens": _zero_tokens(),
                        "_actual_cost": None,
                        "_unresolved_reason": payload.get("reason"),
                    },
                )
            )
            continue
        generation_id = event["generation_id"]
        if not generation_id:
            unresolved_count += 1
            ordered.append((f"unresolved:{event['id']}", payload))
            continue
        if generation_id in generations_by_id:
            merged = _merge_generation(generations_by_id[generation_id], payload)
            generations_by_id[generation_id] = merged
            for index, (key, _) in enumerate(ordered):
                if key == generation_id:
                    ordered[index] = (key, merged)
                    break
        else:
            generations_by_id[generation_id] = payload
            ordered.append((generation_id, payload))
    if not ordered:
        return None

    generations: list[dict[str, Any]] = []
    for sequence, (_, record) in enumerate(ordered, start=1):
        generation_id = record.get("generation_id")
        cost = _decimal_cost(record.get("_actual_cost"))
        if record.get("_evidence_conflict"):
            cost_payload: dict[str, Any] = {
                "status": "cost_unavailable",
                "reason": "duplicate_generation_conflict",
                "fully_reconciled": False,
            }
        elif generation_id and cost is not None:
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
        generations.append(
            {
                "sequence": sequence,
                "generation_id": generation_id,
                "executed_provider": record.get("executed_provider"),
                "executed_model": record.get("executed_model"),
                "tokens": record.get("tokens") or _zero_tokens(),
                "cost": cost_payload,
            }
        )

    token_fields = ("input", "output", "cache_read", "cache_write", "reasoning", "total")
    tokens = {
        field: sum(int(generation["tokens"].get(field, 0)) for generation in generations)
        for field in token_fields
    }
    generation_ids = [
        generation["generation_id"]
        for generation in generations
        if generation["generation_id"]
    ]
    models = list(
        dict.fromkeys(
            generation["executed_model"]
            for generation in generations
            if generation["executed_model"]
        )
    )
    providers = list(
        dict.fromkeys(
            generation["executed_provider"]
            for generation in generations
            if generation["executed_provider"]
        )
    )
    actual_costs = [
        _decimal_cost(generation["cost"].get("amount_usd"))
        for generation in generations
        if generation["cost"]["status"] == "actual"
    ]
    if (
        unresolved_count == 0
        and generations
        and len(actual_costs) == len(generations)
        and all(cost is not None for cost in actual_costs)
    ):
        amount = sum((cost for cost in actual_costs if cost is not None), Decimal("0"))
        aggregate_cost: dict[str, Any] = {
            "status": "actual",
            "amount_micro_usd": _micro_usd(amount),
            "amount_usd": _decimal_string(amount),
            "currency": "USD",
            "source": "openrouter_usage",
            "fully_reconciled": True,
        }
    elif unresolved_count == 0 and generation_ids and len(generation_ids) == len(generations):
        aggregate_cost = {
            "status": "pending",
            "reason": "generation_lookup_pending",
            "generation_ids": generation_ids,
            "fully_reconciled": False,
        }
    else:
        aggregate_cost = {
            "status": "cost_unavailable",
            "reason": "upstream_generation_id_missing",
            "generation_ids": generation_ids,
            "fully_reconciled": False,
        }
    stable_accounting_id = "hermesacct_" + hashlib.sha256(
        request_key.encode("utf-8")
    ).hexdigest()[:32]
    return {
        "schema_version": 2,
        "request_id": stable_accounting_id,
        "runtime": {
            "tier": row["runtime_tier"] or os.getenv("HERMES_RUNTIME_TIER", "unknown").strip().lower(),
            "runtime_id": row["runtime_id"] or os.getenv("HERMES_RUNTIME_ID", "unknown").strip(),
        },
        "model_execution": {
            "configured_provider": "openrouter",
            "configured_model": row["configured_model"] or "unknown",
            "identity_status": "provider_reported" if models else "configured_only",
            "executed_providers": providers,
            "provider_reported_models": models,
            "upstream_generation_ids": generation_ids,
        },
        "generations": generations,
        "tokens": tokens,
        "cost": aggregate_cost,
    }


def _safe_request_view(request_key: str, *, include_events: bool = True) -> dict[str, Any]:
    request_key = normalize_request_key(request_key)
    row, events = _request_row_and_events(request_key)
    accounting = _load(row["accounting_json"])
    if not isinstance(accounting, dict):
        accounting = _accounting_from_events(request_key, row, events)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "request_key": request_key,
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "failure_code": row["failure_code"],
        "runtime": {
            "tier": row["runtime_tier"],
            "runtime_id": row["runtime_id"],
            "configured_provider": row["configured_provider"],
            "configured_model": row["configured_model"],
        },
        "accounting": accounting,
    }
    if include_events:
        result["evidence"] = {
            "generation_ids": list(
                dict.fromkeys(
                    event["generation_id"]
                    for event in events
                    if event["generation_id"]
                )
            ),
            "event_count": len(events),
            "has_unresolved_attempt": any(event["kind"] == "unresolved" for event in events),
        }
    return result


def get_request_view(request_key: str) -> dict[str, Any]:
    return _safe_request_view(request_key)


def internal_auth_token(api_server_key: str | None = None) -> str | None:
    configured = os.getenv("HERMES_ACCOUNTING_INTERNAL_TOKEN", "").strip()
    fallback = str(api_server_key or "").strip()
    return configured or fallback or None


def internal_authorized(authorization_header: str | None, api_server_key: str | None = None) -> bool:
    expected = internal_auth_token(api_server_key)
    if expected is None or not authorization_header:
        return False
    scheme, separator, supplied = authorization_header.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _lookup_generation_http(generation_id: str) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise LookupError("openrouter_api_key_missing")
    base_url = os.getenv("OPENROUTER_API_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).strip().rstrip("/")
    url = f"{base_url}/generation?{urllib.parse.urlencode({'id': generation_id})}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "hermes-durable-accounting/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise LookupError(f"openrouter_http_{error.code}", error.code) from None
    except urllib.error.URLError:
        raise LookupError("openrouter_network_error") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LookupError("openrouter_invalid_json") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or data.get("id") != generation_id:
        raise LookupError("openrouter_generation_mismatch")
    cost = _decimal_cost(data.get("total_cost"))
    if cost is None:
        raise LookupError("openrouter_cost_missing")
    try:
        input_tokens = max(
            0, int(data.get("native_tokens_prompt") or data.get("tokens_prompt") or 0)
        )
        output_tokens = max(
            0,
            int(
                data.get("native_tokens_completion")
                or data.get("tokens_completion")
                or 0
            ),
        )
        cache_read = max(0, int(data.get("native_tokens_cached") or 0))
        reasoning = max(0, int(data.get("native_tokens_reasoning") or 0))
    except (TypeError, ValueError, OverflowError):
        raise LookupError("openrouter_usage_invalid") from None
    cache_read = min(cache_read, input_tokens)
    return {
        "generation_id": generation_id,
        "executed_provider": str(data.get("provider_name") or "").strip() or None,
        "executed_model": str(data.get("model") or "").strip() or None,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read,
            "cache_write": 0,
            "reasoning": reasoning,
            "total": input_tokens + output_tokens,
        },
        "cost": cost,
    }


def _record_reconciliation_event(
    request_key: str,
    generation_id: str | None,
    outcome: str,
    status_code: int | None = None,
    error_code: str | None = None,
) -> None:
    with contextlib.closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO accounting_reconciliation_event (
              request_key, generation_id, outcome, http_status, error_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_key, generation_id, outcome, status_code, error_code, _now()),
        )


def _persist_accounting(request_key: str, accounting: dict[str, Any]) -> None:
    with contextlib.closing(_connect()) as connection:
        connection.execute(
            """
            UPDATE accounting_request SET accounting_json = ?, updated_at = ?
            WHERE request_key = ?
            """,
            (_dump(accounting), _now(), request_key),
        )


def reconcile_request(
    request_key: str,
    lookup: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_key = normalize_request_key(request_key)
    view = _safe_request_view(request_key)
    accounting = view.get("accounting")
    if not isinstance(accounting, dict):
        return {**view, "reconciliation": {"status": "no_evidence", "lookups": []}}
    aggregate_cost = accounting.get("cost")
    if isinstance(aggregate_cost, dict) and aggregate_cost.get("status") == "actual":
        return {**view, "reconciliation": {"status": "actual", "lookups": []}}
    generations = accounting.get("generations")
    if not isinstance(generations, list):
        return {**view, "reconciliation": {"status": "invalid_evidence", "lookups": []}}
    has_missing_generation = any(
        not generation.get("generation_id") for generation in generations
    )
    lookup = lookup or _lookup_generation_http
    lookup_results: list[dict[str, Any]] = []
    changed = False
    for generation in generations:
        if not generation.get("generation_id"):
            continue
        if generation.get("cost", {}).get("status") == "actual":
            continue
        generation_id = generation["generation_id"]
        try:
            resolved = lookup(generation_id)
            cost = _decimal_cost(resolved.get("cost"))
            if cost is None:
                raise LookupError("openrouter_cost_missing")
            generation["executed_provider"] = resolved.get("executed_provider")
            generation["executed_model"] = resolved.get("executed_model")
            generation["tokens"] = resolved.get("tokens") or _zero_tokens()
            generation["cost"] = {
                "status": "actual",
                "amount_micro_usd": _micro_usd(cost),
                "amount_usd": _decimal_string(cost),
                "source": "openrouter_usage",
                "fully_reconciled": True,
            }
            changed = True
            lookup_results.append({"generation_id": generation_id, "status": "actual"})
            _record_reconciliation_event(request_key, generation_id, "actual")
        except LookupError as error:
            lookup_results.append(
                {
                    "generation_id": generation_id,
                    "status": "pending",
                    "error_code": error.code,
                    "http_status": error.status_code,
                }
            )
            _record_reconciliation_event(
                request_key,
                generation_id,
                "pending",
                error.status_code,
                error.code,
            )
    if changed:
        models = list(
            dict.fromkeys(
                generation.get("executed_model")
                for generation in generations
                if generation.get("executed_model")
            )
        )
        providers = list(
            dict.fromkeys(
                generation.get("executed_provider")
                for generation in generations
                if generation.get("executed_provider")
            )
        )
        accounting["model_execution"]["provider_reported_models"] = models
        accounting["model_execution"]["executed_providers"] = providers
        accounting["model_execution"]["identity_status"] = (
            "provider_reported" if models else "configured_only"
        )
        token_fields = ("input", "output", "cache_read", "cache_write", "reasoning", "total")
        accounting["tokens"] = {
            field: sum(int(generation["tokens"].get(field, 0)) for generation in generations)
            for field in token_fields
        }
        if all(generation.get("cost", {}).get("status") == "actual" for generation in generations):
            costs = [
                _decimal_cost(generation["cost"]["amount_usd"])
                for generation in generations
            ]
            amount = sum((cost for cost in costs if cost is not None), Decimal("0"))
            accounting["cost"] = {
                "status": "actual",
                "amount_micro_usd": _micro_usd(amount),
                "amount_usd": _decimal_string(amount),
                "currency": "USD",
                "source": "openrouter_usage",
                "fully_reconciled": True,
            }
        _persist_accounting(request_key, accounting)
        view = _safe_request_view(request_key)
    final_cost = (view.get("accounting") or {}).get("cost") or {}
    if has_missing_generation:
        reconciliation = {
            "status": "manual_review",
            "reason": "upstream_generation_id_missing",
            "lookups": lookup_results,
        }
    else:
        status = "actual" if final_cost.get("status") == "actual" else "pending"
        reconciliation = {"status": status, "lookups": lookup_results}
    return {**view, "reconciliation": reconciliation}
