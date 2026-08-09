#!/usr/bin/env python3
"""Validate the accounting fields that Cabinet may accept from Hermes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def invalid(message: str) -> int:
    print(f"INVALID: {message}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return invalid(str(exc))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return invalid("schema_version must be 1")
    if not value.get("request_id"):
        return invalid("request_id is required")
    runtime = value.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("tier") not in {"economy", "balanced", "strong"}:
        return invalid("runtime tier is invalid")
    execution = value.get("model_execution")
    if not isinstance(execution, dict) or execution.get("configured_provider") != "openrouter":
        return invalid("configured provider must be openrouter")
    if execution.get("identity_status") == "configured_only":
        if execution.get("provider_reported_models") or execution.get("upstream_generation_ids"):
            return invalid("configured_only identity cannot claim provider-reported values")
    elif execution.get("identity_status") != "provider_reported":
        return invalid("model identity status is invalid")
    tokens = value.get("tokens")
    token_names = {"input", "output", "cache_read", "cache_write", "reasoning", "total"}
    if not isinstance(tokens, dict) or set(tokens) != token_names:
        return invalid("token buckets are incomplete")
    if any(isinstance(tokens[name], bool) or not isinstance(tokens[name], int) or tokens[name] < 0 for name in token_names):
        return invalid("token buckets must be non-negative integers")
    if tokens["total"] != tokens["input"] + tokens["output"] + tokens["cache_read"] + tokens["cache_write"]:
        return invalid("token total does not match billable token buckets")
    cost = value.get("cost")
    if not isinstance(cost, dict):
        return invalid("cost is required")
    if cost.get("status") == "actual":
        if set(cost) != {"status", "amount_usd", "currency", "source", "fully_reconciled"}:
            return invalid("actual cost fields are incomplete")
        if not isinstance(cost.get("amount_usd"), str) or not re.fullmatch(r"[0-9]+(\.[0-9]{1,12})?", cost["amount_usd"]):
            return invalid("amount_usd must be a non-negative decimal string")
        if cost.get("currency") != "USD" or cost.get("source") not in {"openrouter_usage", "openrouter_generation"}:
            return invalid("actual cost source or currency is invalid")
        if cost.get("fully_reconciled") is not True:
            return invalid("actual cost must be fully reconciled")
    elif cost.get("status") == "cost_unavailable":
        reasons = {
            "upstream_cost_not_propagated",
            "upstream_generation_id_missing",
            "generation_lookup_pending",
            "generation_lookup_failed",
            "partial_agent_run",
        }
        if set(cost) != {"status", "reason"} or cost.get("reason") not in reasons:
            return invalid("cost_unavailable reason is invalid")
    else:
        return invalid("cost status must be actual or cost_unavailable")
    print(f"OK: {args.result} contains explicit Hermes accounting status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
