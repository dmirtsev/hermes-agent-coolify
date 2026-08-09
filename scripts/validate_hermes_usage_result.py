#!/usr/bin/env python3
"""Validate accounting output before Cabinet treats it as financial evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


TOKEN_NAMES = {"input", "output", "cache_read", "cache_write", "reasoning", "total"}
MICRO_USD = Decimal("1000000")


class ValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_tokens(tokens: Any, label: str) -> None:
    require(isinstance(tokens, dict) and set(tokens) == TOKEN_NAMES, f"{label} token buckets are incomplete")
    require(
        all(not isinstance(tokens[name], bool) and isinstance(tokens[name], int) and tokens[name] >= 0 for name in TOKEN_NAMES),
        f"{label} token buckets must be non-negative integers",
    )
    require(tokens["total"] == tokens["input"] + tokens["output"], f"{label} total must equal input + output")
    require(tokens["cache_read"] <= tokens["input"], f"{label} cache_read cannot exceed input")


def validate_actual_cost(cost: dict[str, Any], aggregate: bool) -> None:
    expected = {"status", "amount_micro_usd", "amount_usd", "source", "fully_reconciled"}
    if aggregate:
        expected.add("currency")
    require(set(cost) == expected, "actual cost fields are incomplete")
    require(cost.get("fully_reconciled") is True, "actual cost must be fully reconciled")
    require(not isinstance(cost.get("amount_micro_usd"), bool) and isinstance(cost.get("amount_micro_usd"), int) and cost["amount_micro_usd"] >= 0, "amount_micro_usd must be a non-negative integer")
    amount_text = cost.get("amount_usd")
    require(isinstance(amount_text, str) and re.fullmatch(r"[0-9]+(\.[0-9]+)?", amount_text) is not None, "amount_usd must be a non-negative decimal string")
    try:
        amount = Decimal(amount_text)
    except InvalidOperation as exc:
        raise ValidationError("amount_usd is not decimal") from exc
    expected_micro = int((amount * MICRO_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    require(cost["amount_micro_usd"] == expected_micro, "amount_micro_usd does not match amount_usd")
    require(cost.get("source") == "openrouter_usage", "actual cost source is invalid")
    if aggregate:
        require(cost.get("currency") == "USD", "actual currency must be USD")


def validate(value: Any) -> None:
    require(isinstance(value, dict), "result must be an object")
    require(value.get("schema_version") == 2, "schema_version must be 2")
    require(isinstance(value.get("request_id"), str) and bool(value["request_id"]), "request_id is required")

    runtime = value.get("runtime")
    require(isinstance(runtime, dict) and set(runtime) == {"tier", "runtime_id"}, "runtime fields are invalid")
    require(runtime.get("tier") in {"economy", "balanced", "strong"}, "runtime tier is invalid")
    require(isinstance(runtime.get("runtime_id"), str) and len(runtime["runtime_id"]) >= 3, "runtime_id is invalid")

    execution = value.get("model_execution")
    execution_fields = {
        "configured_provider", "configured_model", "identity_status",
        "executed_providers", "provider_reported_models", "upstream_generation_ids",
    }
    require(isinstance(execution, dict) and set(execution) == execution_fields, "model_execution fields are invalid")
    require(execution.get("configured_provider") == "openrouter", "configured provider must be openrouter")
    require(isinstance(execution.get("configured_model"), str) and len(execution["configured_model"]) >= 3, "configured model is invalid")
    for field in ("executed_providers", "provider_reported_models", "upstream_generation_ids"):
        items = execution.get(field)
        require(isinstance(items, list) and all(isinstance(item, str) and item for item in items), f"{field} is invalid")
        require(len(items) == len(set(items)), f"{field} must be unique")
    identity_status = execution.get("identity_status")
    require(identity_status in {"configured_only", "provider_reported"}, "model identity status is invalid")
    require((identity_status == "provider_reported") == bool(execution["provider_reported_models"]), "identity status disagrees with provider-reported models")

    generations = value.get("generations")
    require(isinstance(generations, list), "generations must be an array")
    seen_ids: set[str] = set()
    actual_decimal_sum = Decimal("0")
    actual_count = 0
    aggregate_tokens = {name: 0 for name in TOKEN_NAMES}
    for index, generation in enumerate(generations, start=1):
        fields = {"sequence", "generation_id", "executed_provider", "executed_model", "tokens", "cost"}
        require(isinstance(generation, dict) and set(generation) == fields, f"generation {index} fields are invalid")
        require(generation.get("sequence") == index, f"generation {index} sequence is invalid")
        generation_id = generation.get("generation_id")
        require(generation_id is None or (isinstance(generation_id, str) and generation_id), f"generation {index} id is invalid")
        if generation_id:
            require(generation_id not in seen_ids, "generation ids must be unique")
            seen_ids.add(generation_id)
        for field in ("executed_provider", "executed_model"):
            item = generation.get(field)
            require(item is None or (isinstance(item, str) and item), f"generation {index} {field} is invalid")
        validate_tokens(generation.get("tokens"), f"generation {index}")
        for name in TOKEN_NAMES:
            aggregate_tokens[name] += generation["tokens"][name]
        cost = generation.get("cost")
        require(isinstance(cost, dict), f"generation {index} cost is required")
        if cost.get("status") == "actual":
            validate_actual_cost(cost, aggregate=False)
            actual_count += 1
            actual_decimal_sum += Decimal(cost["amount_usd"])
        elif cost.get("status") == "pending":
            require(set(cost) == {"status", "reason", "fully_reconciled"}, "pending generation cost fields are invalid")
            require(generation_id is not None and cost.get("reason") == "generation_lookup_pending" and cost.get("fully_reconciled") is False, "pending generation must have an id")
        elif cost.get("status") == "cost_unavailable":
            require(set(cost) == {"status", "reason", "fully_reconciled"}, "unavailable generation cost fields are invalid")
            reason = cost.get("reason")
            require(
                cost.get("fully_reconciled") is False
                and (
                    (generation_id is None and reason == "upstream_generation_id_missing")
                    or (generation_id is not None and reason == "duplicate_generation_conflict")
                ),
                "unavailable generation reason is invalid",
            )
        else:
            raise ValidationError(f"generation {index} cost status is invalid")

    require(execution["upstream_generation_ids"] == list(dict.fromkeys(g["generation_id"] for g in generations if g["generation_id"])), "aggregate generation ids do not match generations")
    require(execution["provider_reported_models"] == list(dict.fromkeys(g["executed_model"] for g in generations if g["executed_model"])), "aggregate models do not match generations")
    require(execution["executed_providers"] == list(dict.fromkeys(g["executed_provider"] for g in generations if g["executed_provider"])), "aggregate providers do not match generations")
    validate_tokens(value.get("tokens"), "aggregate")
    require(value["tokens"] == aggregate_tokens, "aggregate tokens do not match generations")

    cost = value.get("cost")
    require(isinstance(cost, dict), "cost is required")
    if cost.get("status") == "actual":
        validate_actual_cost(cost, aggregate=True)
        require(bool(generations) and actual_count == len(generations), "actual aggregate requires actual cost for every generation")
        require(Decimal(cost["amount_usd"]) == actual_decimal_sum, "aggregate amount_usd does not match generations")
    elif cost.get("status") == "pending":
        require(set(cost) == {"status", "reason", "generation_ids", "fully_reconciled"}, "pending aggregate fields are invalid")
        require(bool(generations) and all(g["generation_id"] for g in generations), "pending aggregate requires ids for every generation")
        require(actual_count < len(generations), "pending aggregate cannot be fully actual")
        require(all(g["cost"]["status"] in {"actual", "pending"} for g in generations), "pending aggregate cannot hide unavailable generation evidence")
        require(cost.get("reason") == "generation_lookup_pending" and cost.get("generation_ids") == execution["upstream_generation_ids"] and cost.get("fully_reconciled") is False, "pending aggregate is inconsistent")
    elif cost.get("status") == "cost_unavailable":
        require(set(cost) == {"status", "reason", "generation_ids", "fully_reconciled"}, "unavailable aggregate fields are invalid")
        require(cost.get("reason") in {"upstream_generation_id_missing", "no_billable_generations", "generation_lookup_failed", "duplicate_generation_conflict"}, "unavailable reason is invalid")
        require(cost.get("generation_ids") == execution["upstream_generation_ids"] and cost.get("fully_reconciled") is False, "unavailable aggregate is inconsistent")
        if cost["reason"] == "no_billable_generations":
            require(not generations, "no_billable_generations requires an empty generation list")
        elif cost["reason"] == "upstream_generation_id_missing":
            require(any(g["generation_id"] is None for g in generations), "missing-id aggregate requires a generation without id")
        elif cost["reason"] == "duplicate_generation_conflict":
            require(any(g["cost"].get("reason") == "duplicate_generation_conflict" for g in generations), "duplicate conflict aggregate requires conflicting generation evidence")
    else:
        raise ValidationError("aggregate cost status is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.result.read_text(encoding="utf-8"))
        validate(value)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.result} contains authoritative Hermes accounting status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
