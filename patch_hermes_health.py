#!/usr/bin/env python3
"""Expose wrapper release evidence through the pinned Hermes health routes.

The repository intentionally wraps a digest-pinned upstream image instead of
vendoring Hermes.  This small, fail-closed patch is therefore applied while
building the wrapper image.  It only augments the two existing health payloads
and aborts the image build if the pinned upstream source no longer matches the
expected anchors.
"""

from __future__ import annotations

import os
from pathlib import Path


TARGET = Path(
    os.environ.get(
        "HERMES_API_SERVER_PATH",
        "/opt/hermes/gateway/platforms/api_server.py",
    )
)

HELPER_ANCHOR = "\n\n# Default settings\n"
HELPER = r'''

def _wrapper_release_evidence() -> Dict[str, Any]:
    """Read the wrapper's public, non-secret release identity."""
    path = Path(
        os.getenv(
            "HERMES_RELEASE_EVIDENCE_PATH",
            os.path.join(os.getenv("HERMES_HOME", "/opt/data"), "release.json"),
        )
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            allowed = {
                "schema_version",
                "service",
                "environment",
                "wrapper_commit",
                "wrapper_build_date",
                "upstream_version",
                "upstream_revision",
                "upstream_image_digest",
                "runtime_started_at",
                "routing",
            }
            return {key: payload[key] for key in allowed if key in payload}
    except Exception:
        pass
    return {
        "schema_version": 1,
        "service": "hermes-agent-coolify",
        "environment": os.getenv("HERMES_DEPLOYMENT_ENVIRONMENT", os.getenv("COOLIFY_BRANCH", "unknown")),
        "wrapper_commit": os.getenv("HERMES_WRAPPER_COMMIT", os.getenv("SOURCE_COMMIT", "unknown")),
        "wrapper_build_date": os.getenv("HERMES_WRAPPER_BUILD_DATE", "unknown"),
        "upstream_version": os.getenv("HERMES_UPSTREAM_VERSION", "unknown"),
        "upstream_revision": os.getenv("HERMES_UPSTREAM_REVISION", "unknown"),
        "upstream_image_digest": os.getenv("HERMES_UPSTREAM_IMAGE_DIGEST", "unknown"),
    }
'''

SIMPLE_HEALTH = '''        return web.json_response(
            {"status": "ok", "platform": "hermes-agent", "version": _hermes_version()}
        )'''
PATCHED_SIMPLE_HEALTH = '''        return web.json_response({
            "status": "ok",
            "platform": "hermes-agent",
            "version": _hermes_version(),
            "release": _wrapper_release_evidence(),
        })'''

DETAILED_VERSION = '''            "version": _hermes_version(),
            "gateway_state": runtime.get("gateway_state"),'''
PATCHED_DETAILED_VERSION = '''            "version": _hermes_version(),
            "release": _wrapper_release_evidence(),
            "gateway_state": runtime.get("gateway_state"),'''


def replace_once(source: str, old: str, new: str, name: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one {name} anchor in {TARGET}, found {count}")
    return source.replace(old, new, 1)


source = TARGET.read_text(encoding="utf-8")
if "def _wrapper_release_evidence()" in source:
    raise SystemExit(f"release evidence patch is already present in {TARGET}")

source = replace_once(source, HELPER_ANCHOR, HELPER + HELPER_ANCHOR, "helper")
source = replace_once(source, SIMPLE_HEALTH, PATCHED_SIMPLE_HEALTH, "simple health")
source = replace_once(source, DETAILED_VERSION, PATCHED_DETAILED_VERSION, "detailed health")
TARGET.write_text(source, encoding="utf-8")
