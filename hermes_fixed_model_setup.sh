#!/command/with-contenv sh
set -eu

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if ! truthy "${HERMES_FIXED_MODEL_ENABLED:-false}"; then
  echo "[hermes-fixed-model] fixed model setup disabled"
  exit 0
fi

HERMES_HOME="${HERMES_HOME:-/opt/data}"
HERMES_FIXED_MODEL_CONFIG_PATH="${HERMES_FIXED_MODEL_CONFIG_PATH:-${HERMES_HOME}/config.yaml}"
HERMES_FIXED_MODEL_BASE_URL="${HERMES_FIXED_MODEL_BASE_URL:-https://openrouter.ai/api/v1}"
export HERMES_FIXED_MODEL_CONFIG_PATH HERMES_FIXED_MODEL_BASE_URL

python3 - <<'PY'
from __future__ import annotations

import os
import pwd
import re
import tempfile
from pathlib import Path

import yaml


def required(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"[hermes-fixed-model] {name} is required")
    return value


tier = required("HERMES_RUNTIME_TIER")
runtime_id = required("HERMES_RUNTIME_ID")
provider = required("HERMES_FIXED_MODEL_PROVIDER")
model_default = required("HERMES_FIXED_MODEL_DEFAULT")
base_url = required("HERMES_FIXED_MODEL_BASE_URL")
max_tokens_raw = required("HERMES_FIXED_MODEL_MAX_TOKENS")

if tier not in {"economy", "balanced", "strong"}:
    raise SystemExit("[hermes-fixed-model] HERMES_RUNTIME_TIER must be economy, balanced, or strong")
if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", runtime_id):
    raise SystemExit("[hermes-fixed-model] HERMES_RUNTIME_ID has an invalid format")
if provider != "openrouter":
    raise SystemExit("[hermes-fixed-model] sprint 1 supports only provider=openrouter")
if not re.fullmatch(r"[A-Za-z0-9._:-]+/[A-Za-z0-9._:/+-]+", model_default):
    raise SystemExit("[hermes-fixed-model] HERMES_FIXED_MODEL_DEFAULT must be an OpenRouter model id")
if base_url != "https://openrouter.ai/api/v1":
    raise SystemExit("[hermes-fixed-model] HERMES_FIXED_MODEL_BASE_URL must be the OpenRouter API URL")
try:
    max_tokens = int(max_tokens_raw)
except ValueError as exc:
    raise SystemExit("[hermes-fixed-model] HERMES_FIXED_MODEL_MAX_TOKENS must be an integer") from exc
if not 1 <= max_tokens <= 32768:
    raise SystemExit("[hermes-fixed-model] HERMES_FIXED_MODEL_MAX_TOKENS must be between 1 and 32768")

config_path = Path(os.environ["HERMES_FIXED_MODEL_CONFIG_PATH"])
config_path.parent.mkdir(parents=True, exist_ok=True)

original_stat = config_path.stat() if config_path.exists() else None
if config_path.exists():
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
else:
    config = {}
if not isinstance(config, dict):
    raise SystemExit("[hermes-fixed-model] Hermes config root must be a mapping")

model = config.setdefault("model", {})
if not isinstance(model, dict):
    raise SystemExit("[hermes-fixed-model] Hermes config model must be a mapping")
model.update(
    {
        "provider": provider,
        "default": model_default,
        "base_url": base_url,
        "max_tokens": max_tokens,
    }
)

fd, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", dir=config_path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
        handle.flush()
        os.fsync(handle.fileno())
    if original_stat is not None:
        os.chmod(temp_name, original_stat.st_mode & 0o777)
        try:
            os.chown(temp_name, original_stat.st_uid, original_stat.st_gid)
        except PermissionError:
            pass
    else:
        os.chmod(temp_name, 0o640)
        try:
            hermes_user = pwd.getpwnam("hermes")
            os.chown(temp_name, hermes_user.pw_uid, hermes_user.pw_gid)
        except (KeyError, PermissionError, OSError):
            pass
    os.replace(temp_name, config_path)
finally:
    try:
        os.unlink(temp_name)
    except FileNotFoundError:
        pass

print(
    "[hermes-fixed-model] "
    f"runtime_id={runtime_id} tier={tier} provider={provider} "
    f"model={model_default} max_tokens={max_tokens}"
)
PY
