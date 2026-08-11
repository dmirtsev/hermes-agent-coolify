#!/usr/bin/env python3
"""Keep the Docker foreground gateway and the s6 reconciler mutually exclusive."""

from __future__ import annotations

from pathlib import Path


TARGET = Path("/opt/hermes/hermes_cli/container_boot.py")
ANCHOR = '    default_should_start = default_prior_state in _AUTOSTART_STATES\n'
PATCH = ANCHOR + (
    '    # The Coolify wrapper runs the default gateway as Docker\'s main\n'
    '    # process.  Do not also start the restored s6 slot.\n'
    '    if os.environ.get("HERMES_GATEWAY_NO_SUPERVISE", "").lower() in ("1", "true", "yes"):\n'
    '        default_should_start = False\n'
)

if not TARGET.is_file():
    raise SystemExit(f"pinned Hermes container boot source is missing: {TARGET}")
source = TARGET.read_text(encoding="utf-8")
if "# The Coolify wrapper runs the default gateway" in source:
    raise SystemExit("container boot foreground-gateway patch is already present")
if source.count(ANCHOR) != 1:
    raise SystemExit("expected exactly one default gateway start anchor")
TARGET.write_text(source.replace(ANCHOR, PATCH), encoding="utf-8")
