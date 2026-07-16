#!/usr/bin/env sh
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HERMES_HOME

TP_KNOWLEDGE_MCP_ENABLED="${TP_KNOWLEDGE_MCP_ENABLED:-false}"
TP_KNOWLEDGE_MCP_NAME="${TP_KNOWLEDGE_MCP_NAME:-tp_knowledge_test}"
TP_KNOWLEDGE_MCP_URL="${TP_KNOWLEDGE_MCP_URL:-}"
TP_KNOWLEDGE_MCP_CONFIG_PATH="${TP_KNOWLEDGE_MCP_CONFIG_PATH:-${HERMES_HOME}/config.yaml}"
export TP_KNOWLEDGE_MCP_ENABLED
export TP_KNOWLEDGE_MCP_NAME
export TP_KNOWLEDGE_MCP_URL
export TP_KNOWLEDGE_MCP_CONFIG_PATH

HERMES_EDGE_BASIC_AUTH_ENABLED="${HERMES_EDGE_BASIC_AUTH_ENABLED:-false}"
HERMES_EDGE_BASIC_AUTH_LISTEN_HOST="${HERMES_EDGE_BASIC_AUTH_LISTEN_HOST:-0.0.0.0}"
HERMES_EDGE_BASIC_AUTH_LISTEN_PORT="${HERMES_EDGE_BASIC_AUTH_LISTEN_PORT:-9119}"
HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST="${HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST:-127.0.0.1}"
HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT="${HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT:-${API_SERVER_PORT:-8642}}"
if [ "${HERMES_EDGE_BASIC_AUTH_ENABLED}" = "true" ] || [ "${HERMES_EDGE_BASIC_AUTH_ENABLED}" = "TRUE" ] || [ "${HERMES_EDGE_BASIC_AUTH_ENABLED}" = "1" ]; then
  echo "[hermes-entrypoint] HERMES_EDGE_BASIC_AUTH_ENABLED is deprecated; use Coolify/Traefik domain protection instead"
fi
HERMES_EDGE_BASIC_AUTH_ENABLED=false
export HERMES_EDGE_BASIC_AUTH_ENABLED
export HERMES_EDGE_BASIC_AUTH_LISTEN_HOST
export HERMES_EDGE_BASIC_AUTH_LISTEN_PORT
export HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST
export HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT

if [ "${API_SERVER_ENABLED:-}" = "true" ] || [ "${API_SERVER_ENABLED:-}" = "TRUE" ] || [ "${API_SERVER_ENABLED:-}" = "1" ]; then
  API_SERVER_PORT=9119
  export API_SERVER_PORT
fi

if [ "${1:-}" = "gateway" ]; then
  shift
  set -- hermes gateway "$@"
fi

if [ "${1:-}" = "hermes" ] && [ "${2:-}" = "gateway" ] && [ "${3:-}" = "run" ]; then
  case " $* " in
    *" --no-supervise "*)
      ;;
    *)
      set -- "$@" --no-supervise
      ;;
  esac
  case " $* " in
    *" --external-supervisor "*)
      ;;
    *)
      set -- "$@" --external-supervisor
      ;;
  esac
fi

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
from pathlib import Path

config_path = Path(os.environ["TP_KNOWLEDGE_MCP_CONFIG_PATH"])
server_name = os.environ["TP_KNOWLEDGE_MCP_NAME"]
server_url = os.environ["TP_KNOWLEDGE_MCP_URL"]

config_path.parent.mkdir(parents=True, exist_ok=True)

try:
    import yaml
except ImportError:
    yaml = None

if yaml is None:
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = f"""mcp_servers:
  {server_name}:
    url: {server_url}
    headers:
      Authorization: Bearer ${{TP_KNOWLEDGE_MCP_TOKEN}}
    enabled: true
    connect_timeout: 60
    timeout: 300
    tools:
      include:
      - knowledge_answer_context
      resources: false
      prompts: false
"""
    if existing.strip():
        if f"\n  {server_name}:" in existing or f"  {server_name}:" in existing:
            print("PyYAML is unavailable; keeping existing MCP config for " + server_name)
        else:
            with config_path.open("a", encoding="utf-8") as fh:
                if not existing.endswith("\n"):
                    fh.write("\n")
                fh.write("\n")
                fh.write(block)
    else:
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

    mcp_servers[server_name] = {
        "url": server_url,
        "headers": {
            "Authorization": "Bearer ${TP_KNOWLEDGE_MCP_TOKEN}",
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

case "${HERMES_EDGE_BASIC_AUTH_ENABLED}" in
  true|TRUE|1|yes|YES|on|ON)
    if [ -z "${HERMES_EDGE_BASIC_AUTH_USERNAME:-}" ]; then
      echo "[hermes-entrypoint] HERMES_EDGE_BASIC_AUTH_USERNAME is required when HERMES_EDGE_BASIC_AUTH_ENABLED=true" >&2
      exit 1
    fi

    if [ -z "${HERMES_EDGE_BASIC_AUTH_PASSWORD:-}" ]; then
      echo "[hermes-entrypoint] HERMES_EDGE_BASIC_AUTH_PASSWORD is required when HERMES_EDGE_BASIC_AUTH_ENABLED=true" >&2
      exit 1
    fi

    if [ ! -f /hermes_basic_auth_proxy.py ]; then
      echo "[hermes-entrypoint] /hermes_basic_auth_proxy.py is missing" >&2
      exit 1
    fi

    echo "[hermes-entrypoint] starting Hermes behind edge Basic Auth proxy on ${HERMES_EDGE_BASIC_AUTH_LISTEN_HOST}:${HERMES_EDGE_BASIC_AUTH_LISTEN_PORT}"
    "$@" &
    HERMES_PID="$!"

    shutdown() {
      kill "${HERMES_PID}" >/dev/null 2>&1 || true
      wait "${HERMES_PID}" >/dev/null 2>&1 || true
    }
    trap shutdown INT TERM EXIT

    if command -v python3 >/dev/null 2>&1; then
      python3 /hermes_basic_auth_proxy.py
    elif command -v python >/dev/null 2>&1; then
      python /hermes_basic_auth_proxy.py
    else
      echo "[hermes-entrypoint] python is required to run edge Basic Auth proxy" >&2
      exit 1
    fi
    ;;
  *)
    exec "$@"
    ;;
esac
