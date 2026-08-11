#!/usr/bin/env python3
"""Smoke-check health and fixed-model evidence for all Hermes tier endpoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from validate_hermes_tiers_manifest import ContractError, TIERS, load_manifest, validate


def read_health(tier: str, endpoint: str, fixture_dir: Path | None, timeout: float) -> dict:
    if fixture_dir:
        path = fixture_dir / f"{tier}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot read health fixture {path}: {exc}") from exc
    url = f"{endpoint.rstrip('/')}/health"
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            if response.status != 200:
                raise ContractError(f"{tier}: {url} returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ContractError(f"{tier}: cannot read {url}: {exc}") from exc


def check_health(tier: str, runtime: dict, payload: dict, environment: str, commit: str) -> None:
    if payload.get("status") != "ok":
        raise ContractError(f"{tier}: health status is not ok")
    release = payload.get("release")
    if not isinstance(release, dict):
        raise ContractError(f"{tier}: health has no release evidence")
    if release.get("environment") != environment:
        raise ContractError(f"{tier}: release environment mismatch")
    if release.get("wrapper_commit") != commit:
        raise ContractError(f"{tier}: wrapper commit mismatch")
    routing = release.get("routing")
    expected = {
        "strategy": "isolated_fixed_runtime",
        "tier": tier,
        "runtime_id": runtime["runtime_id"],
        "provider": runtime["provider"],
        "model": runtime["model_id"],
        "max_tokens": runtime["max_tokens"],
        "fixed_model_validated": True,
    }
    if not isinstance(routing, dict):
        raise ContractError(f"{tier}: health has no fixed-model routing evidence")
    mismatches = {
        key: {"expected": value, "actual": routing.get(key)}
        for key, value in expected.items()
        if routing.get(key) != value
    }
    if mismatches:
        raise ContractError(f"{tier}: routing evidence mismatch: {mismatches}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        validate(manifest, deployment_ready=False)
        commit = args.expected_commit or manifest["wrapper_commit"]
        if commit == "__SOURCE_COMMIT__":
            raise ContractError("provide --expected-commit when the manifest uses __SOURCE_COMMIT__")
        for tier in TIERS:
            runtime = manifest["runtimes"][tier]
            payload = read_health(tier, runtime["endpoint_url"], args.fixture_dir, args.timeout)
            check_health(tier, runtime, payload, manifest["environment"], commit)
            print(
                f"OK {tier}: runtime={runtime['runtime_id']} "
                f"model={runtime['model_id']} commit={commit[:12]}"
            )
    except ContractError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
