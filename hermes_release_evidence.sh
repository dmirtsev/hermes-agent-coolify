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

import yaml


def clean(name: str, default: str = "unknown", max_length: int = 256) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value or len(value) > max_length or not re.fullmatch(r"[A-Za-z0-9._:/@+-]+", value):
        return default
    return value


def truthy(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def fixed_model_evidence() -> dict:
    enabled = truthy("HERMES_FIXED_MODEL_ENABLED")
    required = truthy("HERMES_FIXED_MODEL_REQUIRED")
    if required and not enabled:
        raise SystemExit(
            "[hermes-release] HERMES_FIXED_MODEL_ENABLED=true is required when "
            "HERMES_FIXED_MODEL_REQUIRED=true"
        )
    if not enabled:
        return {
            "strategy": "legacy_global_config",
            "fixed_model_validated": False,
        }

    tier = clean("HERMES_RUNTIME_TIER")
    runtime_id = clean("HERMES_RUNTIME_ID")
    expected_provider = clean("HERMES_FIXED_MODEL_PROVIDER")
    expected_model = clean("HERMES_FIXED_MODEL_DEFAULT")
    expected_max_tokens = clean("HERMES_FIXED_MODEL_MAX_TOKENS")
    if tier not in {"economy", "balanced", "strong"}:
        raise SystemExit("[hermes-release] invalid HERMES_RUNTIME_TIER")
    if "unknown" in {runtime_id, expected_provider, expected_model, expected_max_tokens}:
        raise SystemExit("[hermes-release] incomplete fixed model contract")

    config_path = Path(
        os.environ.get("HERMES_FIXED_MODEL_CONFIG_PATH")
        or Path(os.environ.get("HERMES_HOME", "/opt/data")) / "config.yaml"
    )
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        model = config["model"]
        actual_provider = str(model["provider"]).strip()
        actual_model = str(model["default"]).strip()
        actual_max_tokens = int(model["max_tokens"])
    except Exception as exc:
        raise SystemExit(f"[hermes-release] cannot read fixed model config: {exc}") from exc

    expected = (expected_provider, expected_model, int(expected_max_tokens))
    actual = (actual_provider, actual_model, actual_max_tokens)
    if actual != expected:
        raise SystemExit(
            "[hermes-release] fixed model mismatch: "
            f"expected provider={expected[0]} model={expected[1]} max_tokens={expected[2]}, "
            f"actual provider={actual[0]} model={actual[1]} max_tokens={actual[2]}"
        )
    return {
        "strategy": "isolated_fixed_runtime",
        "tier": tier,
        "runtime_id": runtime_id,
        "provider": actual_provider,
        "model": actual_model,
        "max_tokens": actual_max_tokens,
        "fixed_model_validated": True,
    }


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
    "routing": fixed_model_evidence(),
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
