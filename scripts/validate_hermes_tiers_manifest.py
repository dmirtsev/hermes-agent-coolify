#!/usr/bin/env python3
"""Validate the dependency-free semantic contract for Hermes tier runtimes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


TIERS = ("economy", "balanced", "strong")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]+/[A-Za-z0-9._:/+-]+$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
PLACEHOLDERS = ("change_me", "__")


class ContractError(ValueError):
    pass


def load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("manifest root must be an object")
    return value


def ensure_exact_keys(value: dict, expected: set[str], context: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        raise ContractError(f"{context} keys mismatch: missing={sorted(missing)} extra={sorted(extra)}")


def contains_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDERS)


def validate(manifest: dict, deployment_ready: bool = False) -> None:
    ensure_exact_keys(
        manifest,
        {"schema_version", "environment", "strategy", "wrapper_commit", "runtimes"},
        "manifest",
    )
    if manifest["schema_version"] != 1:
        raise ContractError("schema_version must be 1")
    if not isinstance(manifest["environment"], str) or not ID_RE.fullmatch(manifest["environment"]):
        raise ContractError("environment has an invalid format")
    if manifest["strategy"] != "isolated_fixed_runtime":
        raise ContractError("strategy must be isolated_fixed_runtime")
    commit = manifest["wrapper_commit"]
    if not isinstance(commit, str) or not (re.fullmatch(r"[0-9a-f]{7,40}", commit) or commit == "__SOURCE_COMMIT__"):
        raise ContractError("wrapper_commit must be a Git SHA or __SOURCE_COMMIT__")
    if deployment_ready and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ContractError("deployment-ready wrapper_commit must be a full 40-character Git SHA")

    runtimes = manifest["runtimes"]
    if not isinstance(runtimes, dict):
        raise ContractError("runtimes must be an object")
    ensure_exact_keys(runtimes, set(TIERS), "runtimes")

    unique_fields = {
        name: set()
        for name in (
            "runtime_id",
            "endpoint_url",
            "volume_name",
            "api_server_key_ref",
            "openrouter_api_key_ref",
        )
    }
    required = {
        "runtime_id",
        "endpoint_url",
        "provider",
        "model_id",
        "max_tokens",
        "volume_name",
        "api_server_key_ref",
        "openrouter_api_key_ref",
    }
    for tier in TIERS:
        runtime = runtimes[tier]
        if not isinstance(runtime, dict):
            raise ContractError(f"runtimes.{tier} must be an object")
        ensure_exact_keys(runtime, required, f"runtimes.{tier}")
        if runtime["provider"] != "openrouter":
            raise ContractError(f"runtimes.{tier}.provider must be openrouter")
        if not isinstance(runtime["runtime_id"], str) or not ID_RE.fullmatch(runtime["runtime_id"]):
            raise ContractError(f"runtimes.{tier}.runtime_id has an invalid format")
        if not isinstance(runtime["volume_name"], str) or not ID_RE.fullmatch(runtime["volume_name"]):
            raise ContractError(f"runtimes.{tier}.volume_name has an invalid format")
        model_id = runtime["model_id"]
        if not isinstance(model_id, str) or not MODEL_RE.fullmatch(model_id):
            raise ContractError(f"runtimes.{tier}.model_id is not an OpenRouter model id")
        if deployment_ready and contains_placeholder(model_id):
            raise ContractError(f"runtimes.{tier}.model_id still contains a placeholder")
        max_tokens = runtime["max_tokens"]
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32768:
            raise ContractError(f"runtimes.{tier}.max_tokens must be an integer from 1 to 32768")

        endpoint = runtime["endpoint_url"]
        if not isinstance(endpoint, str):
            raise ContractError(f"runtimes.{tier}.endpoint_url must be a string")
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ContractError(f"runtimes.{tier}.endpoint_url must be a clean HTTPS origin")
        normalized_endpoint = endpoint.rstrip("/")
        runtime["endpoint_url"] = normalized_endpoint

        for ref_name in ("api_server_key_ref", "openrouter_api_key_ref"):
            ref = runtime[ref_name]
            if not isinstance(ref, str) or not ref.startswith("coolify://") or contains_placeholder(ref):
                raise ContractError(f"runtimes.{tier}.{ref_name} must be a non-secret Coolify reference")

        for name, seen in unique_fields.items():
            value = runtime[name]
            if value in seen:
                raise ContractError(f"runtimes.{tier}.{name} must be unique")
            seen.add(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--deployment-ready", action="store_true")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        validate(manifest, deployment_ready=args.deployment_ready)
    except ContractError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.manifest} defines economy, balanced, and strong isolated runtimes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
