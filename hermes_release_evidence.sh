#!/command/with-contenv sh
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
HERMES_RELEASE_EVIDENCE_PATH="${HERMES_RELEASE_EVIDENCE_PATH:-${HERMES_HOME}/release.json}"
export HERMES_RELEASE_EVIDENCE_PATH

mkdir -p "$(dirname "${HERMES_RELEASE_EVIDENCE_PATH}")"

python3 - <<'PY'
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def clean(name: str, default: str = "unknown", max_length: int = 256) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value or len(value) > max_length or not re.fullmatch(r"[A-Za-z0-9._:/@+-]+", value):
        return default
    return value


def truthy(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


path = Path(os.environ["HERMES_RELEASE_EVIDENCE_PATH"])
wrapper_commit = clean("HERMES_WRAPPER_COMMIT")
if wrapper_commit == "unknown":
    wrapper_commit = clean("SOURCE_COMMIT")

if truthy("HERMES_RELEASE_EVIDENCE_REQUIRED") and wrapper_commit == "unknown":
    raise SystemExit(
        "[hermes-release] wrapper commit is required; set HERMES_WRAPPER_COMMIT=$SOURCE_COMMIT "
        "at runtime or enable Coolify 'Include Source Commit in Build'"
    )

evidence = {
    "schema_version": 1,
    "service": "hermes-agent-coolify",
    "environment": clean("HERMES_DEPLOYMENT_ENVIRONMENT", clean("COOLIFY_BRANCH")),
    "wrapper_commit": wrapper_commit,
    "wrapper_build_date": clean("HERMES_WRAPPER_BUILD_DATE"),
    "upstream_version": clean("HERMES_UPSTREAM_VERSION"),
    "upstream_revision": clean("HERMES_UPSTREAM_REVISION"),
    "upstream_image_digest": clean("HERMES_UPSTREAM_IMAGE_DIGEST"),
    "runtime_started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}

path.parent.mkdir(parents=True, exist_ok=True)
fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_name, 0o644)
    os.replace(temp_name, path)
finally:
    try:
        os.unlink(temp_name)
    except FileNotFoundError:
        pass

print(
    "[hermes-release] "
    f"environment={evidence['environment']} "
    f"wrapper_commit={evidence['wrapper_commit']} "
    f"upstream_version={evidence['upstream_version']} "
    f"upstream_revision={evidence['upstream_revision']}"
)
PY
