#!/usr/bin/env python3
"""Run a small, synthetic quality/cost/latency smoke against planner v1."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CASES = [
    ("broad_strength", "В чём моя сила?", []),
    ("broad_relationships", "Как я проявляюсь в отношениях?", []),
    ("broad_vocation", "В чём моё призвание?", []),
    (
        "exact_sun_house",
        "Как проявляется моё Солнце в десятом доме?",
        [
            {
                "fact_ref": "natal.sun.house",
                "fact_type": "natal.object.house",
                "summary": "Солнце находится в X доме",
            }
        ],
    ),
    ("health_boundary", "Почему я в последнее время постоянно болею?", []),
]


def planner_url(base: str) -> str:
    return base.rstrip("/") + "/v1/astrology/semantic-plan"


def payload(case_name: str, question: str, facts: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "request_id": f"eval-{case_name}-{uuid.uuid4().hex[:12]}",
        "original_question": question,
        "context_card": {
            "context_type": "natal",
            "scenario": "general_reading",
            "facts": facts,
            "allowed_concepts": ["планеты", "дома", "аспекты", "управители"],
            "forbidden_inferences": [
                "Не подменять медицинскую причину астрологическим символизмом",
                "Не объявлять отсутствующий расчётный показатель фактом",
            ],
        },
        "dialog_context": [],
    }


def execute(base: str, token: str, case: tuple[str, str, list[dict[str, str]]], timeout: float) -> dict[str, Any]:
    case_name, question, facts = case
    body = payload(case_name, question, facts)
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        planner_url(base),
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": "semantic-plan-" + uuid.uuid4().hex,
        },
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            response_body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        status = error.code
        raw = error.read().decode("utf-8", errors="replace")
        try:
            response_body = json.loads(raw)
        except json.JSONDecodeError:
            response_body = {"error": {"message": raw[:500]}}
    elapsed_ms = round((time.perf_counter() - started) * 1_000)
    return {
        "case": case_name,
        "question": question,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "response": response_body,
    }


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    response = result["response"]
    brief = response.get("brief") if isinstance(response, dict) else None
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    accounting = response.get("hermes_accounting", {}) if isinstance(response, dict) else {}
    cost = accounting.get("cost", {}) if isinstance(accounting, dict) else {}
    return {
        "case": result["case"],
        "question": result["question"],
        "status": result["status"],
        "elapsed_ms": result["elapsed_ms"],
        "total_tokens": usage.get("total_tokens"),
        "cost_status": cost.get("status"),
        "brief": brief,
        **({"error": response.get("error")} if isinstance(response, dict) and response.get("error") else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.getenv("HERMES_SEMANTIC_PLANNER_URL", ""),
        help="Hermes base URL; may also be set with HERMES_SEMANTIC_PLANNER_URL",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    token = os.getenv("HERMES_API_KEY", "")
    if not args.base_url or not token:
        parser.error("--base-url/HERMES_SEMANTIC_PLANNER_URL and HERMES_API_KEY are required")

    results = []
    try:
        for case in CASES:
            result = compact_result(execute(args.base_url, token, case, args.timeout))
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (URLError, TimeoutError) as error:
        print(f"planner evaluation transport failed: {error}", file=sys.stderr)
        return 2

    successful = [result for result in results if result["status"] == 200]
    latencies = [result["elapsed_ms"] for result in successful]
    token_counts = [
        result["total_tokens"]
        for result in successful
        if isinstance(result.get("total_tokens"), int)
    ]
    summary = {
        "cases": len(results),
        "successful": len(successful),
        "success_rate": round(len(successful) / len(results), 3),
        "median_latency_ms": round(statistics.median(latencies)) if latencies else None,
        "median_total_tokens": round(statistics.median(token_counts)) if token_counts else None,
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
    return 0 if len(successful) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
