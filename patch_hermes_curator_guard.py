#!/usr/bin/env python3
"""Bound the number of LLM turns in the pinned upstream Curator."""

import os
from pathlib import Path


TARGET = Path(
    os.environ.get("HERMES_CURATOR_SOURCE_PATH", "/opt/hermes/agent/curator.py")
)
ANCHOR = "def _run_llm_review(prompt: str) -> Dict[str, Any]:\n"
HELPER = '''def _server_curator_max_iterations() -> int:\n    """Return a fail-closed server cap for one Curator pass."""\n    raw = os.environ.get("HERMES_CURATOR_MAX_ITERATIONS", "12").strip()\n    try:\n        value = int(raw)\n    except ValueError as exc:\n        raise RuntimeError("HERMES_CURATOR_MAX_ITERATIONS must be an integer") from exc\n    if not 1 <= value <= 25:\n        raise RuntimeError("HERMES_CURATOR_MAX_ITERATIONS must be between 1 and 25")\n    return value\n\n\n'''
OLD_LIMIT = "            max_iterations=9999,\n"
NEW_LIMIT = "            max_iterations=_server_curator_max_iterations(),\n"

if not TARGET.is_file():
    raise SystemExit(f"pinned Hermes Curator source is missing: {TARGET}")
source = TARGET.read_text(encoding="utf-8")
if "def _server_curator_max_iterations" in source:
    raise SystemExit("Curator server guard patch is already present")
if source.count(ANCHOR) != 1:
    raise SystemExit("expected exactly one Curator review anchor")
if source.count(OLD_LIMIT) != 1:
    raise SystemExit("expected exactly one upstream Curator iteration limit")
source = source.replace(ANCHOR, HELPER + ANCHOR)
source = source.replace(OLD_LIMIT, NEW_LIMIT)
TARGET.write_text(source, encoding="utf-8")
