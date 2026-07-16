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

exec "$@"
