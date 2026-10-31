#!/usr/bin/env python3
"""Validate external V4.1 calibration evidence without certifying it.

This is deliberately a read-only gate: it never writes the route registry and
never calls a provider. A human must review the output before certification.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MODEL = "deepseek/deepseek-v4.1-flash"
MIN_WINDOW_128K = 142592  # 128000 input + 8192 output + 6400 margin


def validate(document: dict) -> list[str]:
    errors = []
    if document.get("syntheticOnly") is not True:
        errors.append("syntheticOnly must be true")
    if document.get("model") != MODEL:
        errors.append("model must be the exact V4.1 slug")
    if not isinstance(document.get("contextWindow"), int):
        errors.append("contextWindow must be an integer")
    elif document["contextWindow"] < MIN_WINDOW_128K:
        errors.append("contextWindow is below the 128k admission requirement")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        return errors
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if case.get("model") != MODEL:
            errors.append(f"{prefix}.model mismatch")
        if not isinstance(case.get("provider"), str) or not case["provider"]:
            errors.append(f"{prefix}.provider missing")
        for key in ("referenceTokens", "providerInputTokens", "admissionUpperTokens"):
            if not isinstance(case.get(key), int) or case[key] < 0:
                errors.append(f"{prefix}.{key} must be a non-negative integer")
        if (isinstance(case.get("providerInputTokens"), int)
                and isinstance(case.get("admissionUpperTokens"), int)
                and case["admissionUpperTokens"] < case["providerInputTokens"]):
            errors.append(f"{prefix}.admissionUpperTokens is below provider input")
        if not isinstance(case.get("messagesSha256"), str) or len(case["messagesSha256"]) != 64:
            errors.append(f"{prefix}.messagesSha256 missing or malformed")
        if not isinstance(case.get("generationId"), str) or not case["generationId"]:
            errors.append(f"{prefix}.generationId missing")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} evidence.json", file=sys.stderr)
        return 2
    document = json.loads(Path(argv[1]).read_text())
    errors = validate(document)
    result = {"model": MODEL, "valid": not errors, "errors": errors,
              "registryUpdated": False, "paidCall": False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
