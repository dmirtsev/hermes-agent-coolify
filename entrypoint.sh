#!/usr/bin/env sh
set -eu

TP_KNOWLEDGE_MCP_NAME="${TP_KNOWLEDGE_MCP_NAME:-tp_knowledge}"
TP_KNOWLEDGE_MCP_URL="${TP_KNOWLEDGE_MCP_URL:-https://mcp-bridge-germes-knowledge.astrogeoagent.ru/mcp}"

if [ "${TP_KNOWLEDGE_MCP_ENABLED:-true}" = "true" ]; then
  echo "[hermes-entrypoint] ensuring MCP server ${TP_KNOWLEDGE_MCP_NAME} -> ${TP_KNOWLEDGE_MCP_URL}"

  if command -v hermes >/dev/null 2>&1; then
    hermes mcp add "${TP_KNOWLEDGE_MCP_NAME}" --url "${TP_KNOWLEDGE_MCP_URL}" || true
    hermes mcp list || true
  else
    echo "[hermes-entrypoint] hermes CLI not found; skipping MCP registration"
  fi
fi

exec gateway run
