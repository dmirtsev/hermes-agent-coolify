#!/usr/bin/env python3
"""Protect the pinned Hermes runtime-status file from stale writers.

The wrapper applies this narrow patch only when the reviewed v0.16.0 source
still has the expected anchors.  Source drift stops the image build rather
than weakening runtime-status ownership silently.
"""

from __future__ import annotations

import os
from textwrap import indent
from pathlib import Path


ROOT = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))
STATUS = Path(
    os.environ.get("HERMES_STATUS_PATH", str(ROOT / "gateway" / "status.py"))
)
GUARD = ROOT / "agent" / "runtime_status_guard.py"

IMPORT_ANCHOR = "from utils import atomic_json_write\n"
WRITE_ANCHOR = "    _write_json_file(path, payload)\n"
GUARD_IMPORT = (
    "from utils import atomic_json_write\n"
    "from agent.runtime_status_guard import (\n"
    "    runtime_status_owner_id,\n"
    "    runtime_status_write_lock,\n"
    "    runtime_status_write_is_foreign,\n"
    ")\n"
)
OWNER_ANCHOR = '    payload["start_time"] = current_record["start_time"]\n'
OWNER_WRITE = OWNER_ANCHOR + '    payload["owner_id"] = runtime_status_owner_id()\n'
PAYLOAD_ANCHOR = "    payload = _read_json_file(path) or _build_runtime_status_record()\n"
GUARDED_WRITE = """    if runtime_status_write_is_foreign(
        _read_json_file(path),
        current_record,
        gateway_state,
    ):
        return
    _write_json_file(path, payload)
"""


def replace_once(source: str, old: str, new: str, name: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one {name} anchor in {STATUS}, found {count}")
    return source.replace(old, new, 1)


if not STATUS.is_file():
    raise SystemExit(f"pinned Hermes status source is missing: {STATUS}")
if not GUARD.is_file():
    raise SystemExit(f"runtime status guard is missing: {GUARD}")

source = STATUS.read_text(encoding="utf-8")
if "runtime_status_write_is_foreign" in source:
    raise SystemExit("runtime-status ownership patch is already present")

source = replace_once(source, IMPORT_ANCHOR, GUARD_IMPORT, "guard import")
start = source.find(PAYLOAD_ANCHOR)
if start < 0:
    raise SystemExit(f"expected runtime status payload anchor in {STATUS}")
end = source.find(WRITE_ANCHOR, start)
if end < 0:
    raise SystemExit(f"expected runtime status write anchor in {STATUS}")
end += len(WRITE_ANCHOR)
body = source[start:end]
body = replace_once(body, OWNER_ANCHOR, OWNER_WRITE, "runtime status owner")
body = replace_once(body, WRITE_ANCHOR, GUARDED_WRITE, "runtime status write")
source = (
    source[:start]
    + "    with runtime_status_write_lock(path):\n"
    + indent(body, "    ")
    + source[end:]
)
STATUS.write_text(source, encoding="utf-8")
