#!/command/with-contenv sh
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HERMES_HOME

TP_KNOWLEDGE_MCP_ENABLED="${TP_KNOWLEDGE_MCP_ENABLED:-false}"
TP_KNOWLEDGE_MCP_NAME="${TP_KNOWLEDGE_MCP_NAME:-tp_knowledge_test}"
TP_KNOWLEDGE_MCP_URL="${TP_KNOWLEDGE_MCP_URL:-}"
TP_KNOWLEDGE_MCP_CONFIG_PATH="${TP_KNOWLEDGE_MCP_CONFIG_PATH:-${HERMES_HOME}/config.yaml}"
TP_KNOWLEDGE_MCP_ENV_PATH="${TP_KNOWLEDGE_MCP_ENV_PATH:-${HERMES_HOME}/.env}"
export TP_KNOWLEDGE_MCP_ENABLED
export TP_KNOWLEDGE_MCP_NAME
export TP_KNOWLEDGE_MCP_URL
export TP_KNOWLEDGE_MCP_CONFIG_PATH
export TP_KNOWLEDGE_MCP_ENV_PATH

API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
API_SERVER_HOST="${API_SERVER_HOST:-0.0.0.0}"
API_SERVER_PORT=9119
HERMES_DASHBOARD=0
export API_SERVER_ENABLED
export API_SERVER_HOST
export API_SERVER_PORT
export HERMES_DASHBOARD

persist_s6_env() {
  if [ -d /run/s6/container_environment ]; then
    printf '%s' "$2" > "/run/s6/container_environment/$1"
  fi
}

rm -f /run/s6/container_environment/HERMES_DASHBOARD \
  /run/s6/container_environment/HERMES_DASHBOARD_PORT \
  /run/s6/container_environment/HERMES_DASHBOARD_HOST \
  /run/s6/container_environment/HERMES_DASHBOARD_BASIC_AUTH_USERNAME \
  /run/s6/container_environment/HERMES_DASHBOARD_BASIC_AUTH_PASSWORD \
  /run/s6/container_environment/HERMES_DASHBOARD_BASIC_AUTH_SECRET 2>/dev/null || true

persist_s6_env API_SERVER_ENABLED "${API_SERVER_ENABLED}"
persist_s6_env API_SERVER_HOST "${API_SERVER_HOST}"
persist_s6_env API_SERVER_PORT "${API_SERVER_PORT}"
persist_s6_env HERMES_DASHBOARD "${HERMES_DASHBOARD}"

PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
export PATH

case "${TP_KNOWLEDGE_MCP_ENABLED}" in
  true|TRUE|1|yes|YES|on|ON)
    if [ -z "${TP_KNOWLEDGE_MCP_URL}" ]; then
      echo "[hermes-entrypoint] TP_KNOWLEDGE_MCP_URL is required when TP_KNOWLEDGE_MCP_ENABLED=true" >&2
      exit 1
    fi

    if [ -z "${TP_KNOWLEDGE_MCP_TOKEN:-}" ]; then
      echo "[hermes-entrypoint] TP_KNOWLEDGE_MCP_TOKEN is required when TP_KNOWLEDGE_MCP_ENABLED=true" >&2
      exit 1
    fi

    mkdir -p "${HERMES_HOME}" "$(dirname "${TP_KNOWLEDGE_MCP_CONFIG_PATH}")"

    echo "[hermes-entrypoint] ensuring MCP server ${TP_KNOWLEDGE_MCP_NAME} -> ${TP_KNOWLEDGE_MCP_URL}"

    if command -v python3 >/dev/null 2>&1; then
      PYTHON_BIN=python3
    elif command -v python >/dev/null 2>&1; then
      PYTHON_BIN=python
    else
      echo "[hermes-entrypoint] python is required to update Hermes config safely" >&2
      exit 1
    fi

    "${PYTHON_BIN}" - <<'PY'
import os
import pwd
import re
from pathlib import Path

config_path = Path(os.environ["TP_KNOWLEDGE_MCP_CONFIG_PATH"])
env_path = Path(os.environ["TP_KNOWLEDGE_MCP_ENV_PATH"])
server_name = os.environ["TP_KNOWLEDGE_MCP_NAME"]
server_url = os.environ["TP_KNOWLEDGE_MCP_URL"]
token = os.environ["TP_KNOWLEDGE_MCP_TOKEN"].strip()

if token.lower().startswith("bearer "):
    token = token[7:].strip()

env_key = "MCP_" + re.sub(r"[^A-Za-z0-9_]", "_", server_name.upper()).strip("_") + "_API_KEY"

config_path.parent.mkdir(parents=True, exist_ok=True)
env_path.parent.mkdir(parents=True, exist_ok=True)

s6_env_dir = Path("/run/s6/container_environment")
if s6_env_dir.is_dir():
    (s6_env_dir / env_key).write_text(token, encoding="utf-8")

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    request = Request(
        server_url,
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            print(f"[hermes-entrypoint] MCP auth probe returned HTTP {response.status}")
    except HTTPError as exc:
        print(f"[hermes-entrypoint] MCP auth probe returned HTTP {exc.code}")
    except URLError as exc:
        print(f"[hermes-entrypoint] MCP auth probe failed: {exc.reason}")
except Exception as exc:
    print(f"[hermes-entrypoint] MCP auth probe skipped: {exc.__class__.__name__}")

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
prefix = env_key + "="
updated = False
next_lines = []
for line in lines:
    if line.startswith(prefix):
        if not updated:
            next_lines.append(prefix + token)
            updated = True
        continue
    next_lines.append(line)
if not updated:
    next_lines.append(prefix + token)
env_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
env_path.chmod(0o640)
try:
    hermes_user = pwd.getpwnam("hermes")
    os.chown(env_path, hermes_user.pw_uid, hermes_user.pw_gid)
except (KeyError, PermissionError, OSError):
    pass

try:
    import yaml
except ImportError:
    yaml = None

if yaml is None:
    block = f"""mcp_servers:
  {server_name}:
    url: {server_url}
    headers:
      Authorization: Bearer ${{{env_key}}}
    enabled: true
    connect_timeout: 60
    timeout: 300
    tools:
      include:
      - knowledge_answer_context
      resources: false
      prompts: false
"""
    config_path.write_text(block, encoding="utf-8")
else:
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    else:
        config = {}

    if not isinstance(config, dict):
        raise SystemExit("Hermes config root must be a mapping")

    mcp_servers = config.setdefault("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        raise SystemExit("Hermes config mcp_servers must be a mapping")

    mcp_servers.clear()
    mcp_servers[server_name] = {
        "url": server_url,
        "headers": {
            "Authorization": f"Bearer ${{{env_key}}}",
        },
        "enabled": True,
        "connect_timeout": 60,
        "timeout": 300,
        "tools": {
            "include": ["knowledge_answer_context"],
            "resources": False,
            "prompts": False,
        },
    }

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False)
PY

    if command -v hermes >/dev/null 2>&1; then
      hermes mcp list || true
    fi
    ;;
  *)
    echo "[hermes-entrypoint] TP Knowledge MCP registration disabled"
    ;;
esac
