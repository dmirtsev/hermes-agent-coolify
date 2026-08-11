#!/usr/bin/env python3
"""Validate the Hermes technical manifest without starting a runtime."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "registry" / "product-capability-manifest.v1.json"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("schema_version") != 1 or manifest.get("repository") != "hermes-agent":
        errors.append("Manifest identity must be hermes-agent v1.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("reviewed_through", ""))):
        errors.append("reviewed_through must be a full Git SHA.")
    files = [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in files)
    keys: set[str] = set()
    for function in manifest.get("functions", []):
        key = function.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in keys:
            errors.append(f"Invalid or duplicate function key: {key!r}")
        keys.add(key)
        if function.get("status") not in {"test", "production", "internal", "planned"}:
            errors.append(f"{key}: invalid status")
        if not isinstance(function.get("policy"), str) or not function["policy"].strip():
            errors.append(f"{key}: policy is required")
        for surface in function.get("surfaces", []):
            if not isinstance(surface, str) or not surface:
                errors.append(f"{key}: invalid surface {surface!r}")
            elif surface not in text and not (ROOT / surface).exists():
                errors.append(f"{key}: surface is not found in Hermes source: {surface}")
    if errors:
        print("Product capability manifest validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"Product capability manifest validated: {len(keys)} Hermes functions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
