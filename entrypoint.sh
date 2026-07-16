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
HERMES_EDGE_BASIC_AUTH_LISTEN_PORT="${HERMES_EDGE_BASIC_AUTH_LISTEN_PORT:-${HERMES_DASHBOARD_PORT:-9119}}"
HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST="${HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST:-127.0.0.1}"
HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT="${HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT:-19119}"
export HERMES_EDGE_BASIC_AUTH_ENABLED
export HERMES_EDGE_BASIC_AUTH_LISTEN_HOST
export HERMES_EDGE_BASIC_AUTH_LISTEN_PORT
export HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST
export HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT

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

import yaml

config_path = Path(os.environ["TP_KNOWLEDGE_MCP_CONFIG_PATH"])
server_name = os.environ["TP_KNOWLEDGE_MCP_NAME"]
server_url = os.environ["TP_KNOWLEDGE_MCP_URL"]

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

config_path.parent.mkdir(parents=True, exist_ok=True)
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

    export HERMES_DASHBOARD_HOST="${HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST}"
    export HERMES_DASHBOARD_PORT="${HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT}"

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
