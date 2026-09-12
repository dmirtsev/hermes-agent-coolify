#!/command/with-contenv sh
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
HERMES_CURATOR_CONFIG_PATH="${HERMES_CURATOR_CONFIG_PATH:-${HERMES_HOME}/config.yaml}"
export HERMES_CURATOR_CONFIG_PATH

python3 - <<'PY'
from __future__ import annotations

import os
import pwd
import re
import tempfile
from pathlib import Path

import yaml


def boolean(name: str, default: str) -> bool:
    raw = str(os.environ.get(name, default)).strip().lower()
    if raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise SystemExit(f"[hermes-curator] {name} must be a boolean")
    return raw in {"1", "true", "yes", "on"}


enabled = boolean("HERMES_CURATOR_ENABLED", "false")
model = str(
    os.environ.get("HERMES_CURATOR_MODEL_DEFAULT")
    or "deepseek/deepseek-v4-flash"
).strip()
base_url = str(
    os.environ.get("HERMES_CURATOR_BASE_URL")
    or "https://openrouter.ai/api/v1"
).strip()
if not re.fullmatch(r"[A-Za-z0-9._:-]+/[A-Za-z0-9._:/+-]+", model):
    raise SystemExit(
        "[hermes-curator] HERMES_CURATOR_MODEL_DEFAULT must be an OpenRouter model id"
    )
if base_url != "https://openrouter.ai/api/v1":
    raise SystemExit("[hermes-curator] HERMES_CURATOR_BASE_URL must be OpenRouter")
try:
    interval_hours = int(os.environ.get("HERMES_CURATOR_INTERVAL_HOURS", "168"))
except ValueError as exc:
    raise SystemExit(
        "[hermes-curator] HERMES_CURATOR_INTERVAL_HOURS must be an integer"
    ) from exc
if not 24 <= interval_hours <= 24 * 365:
    raise SystemExit(
        "[hermes-curator] HERMES_CURATOR_INTERVAL_HOURS must be between 24 and 8760"
    )

path = Path(os.environ["HERMES_CURATOR_CONFIG_PATH"])
path.parent.mkdir(parents=True, exist_ok=True)
original_stat = path.stat() if path.exists() else None
config = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
if not isinstance(config, dict):
    raise SystemExit("[hermes-curator] Hermes config root must be a mapping")

curator = config.setdefault("curator", {})
auxiliary = config.setdefault("auxiliary", {})
if not isinstance(curator, dict) or not isinstance(auxiliary, dict):
    raise SystemExit("[hermes-curator] curator and auxiliary must be mappings")
slot = auxiliary.setdefault("curator", {})
if not isinstance(slot, dict):
    raise SystemExit("[hermes-curator] auxiliary.curator must be a mapping")

curator.update(
    {
        "enabled": enabled,
        "interval_hours": interval_hours,
        "prune_builtins": False,
    }
)
slot.update(
    {
        "provider": "openrouter",
        "model": model,
        "base_url": base_url,
    }
)

fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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
            user = pwd.getpwnam("hermes")
            os.chown(temp_name, user.pw_uid, user.pw_gid)
        except (KeyError, PermissionError, OSError):
            pass
    os.replace(temp_name, path)
finally:
    try:
        os.unlink(temp_name)
    except FileNotFoundError:
        pass

print(
    "[hermes-curator] "
    f"enabled={str(enabled).lower()} provider=openrouter model={model} "
    f"interval_hours={interval_hours} prune_builtins=false"
)
PY
