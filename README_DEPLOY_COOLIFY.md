# Hermes Agent for Coolify

This repository is a tiny Coolify wrapper around the official Hermes Agent Docker image.

It does not copy Hermes source code.

The wrapper starts Hermes through `entrypoint.sh`, then executes `gateway run`.
The entrypoint is responsible for optional MCP registration before the gateway starts.

## Coolify

Create a new Application:

- Repository: `dmirtsev/hermes-agent-coolify`
- Branch: `main`
- Build pack: Dockerfile
- Domain: `hermes.astrogeoagent.ru`
- Ports Exposes: `9119`

## Persistent storage

Add a persistent volume:

```text
/opt/data
```

Hermes stores config, API keys, sessions, memories, skills and logs in `/opt/data`.

## Environment variables

Minimum dashboard configuration:

```env
HERMES_DASHBOARD=1
HERMES_DASHBOARD_PORT=9119
HERMES_DASHBOARD_HOST=0.0.0.0
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=CHANGE_ME
HERMES_DASHBOARD_BASIC_AUTH_SECRET=CHANGE_ME_LONG_RANDOM_SECRET
```

Optional OpenAI/OpenRouter keys can be added later through Hermes setup/dashboard or environment configuration.

## Test TP Knowledge MCP config

Use a separate Coolify application for the test contour:

```text
Repository: dmirtsev/hermes-agent-coolify
Branch: test
Domain: <separate test Hermes domain>
Ports Exposes: 9119
```

Set these variables in the test Coolify application. Do not commit their values:

```env
TP_KNOWLEDGE_MCP_ENABLED=true
TP_KNOWLEDGE_MCP_URL=https://test-mcp-bridge-germes-knowledge.astrogeoagent.ru/mcp
TP_KNOWLEDGE_MCP_TOKEN=<secret from the test MCP_BRIDGE_TOKEN>
TP_KNOWLEDGE_MCP_NAME=tp_knowledge_test
```

At startup, `entrypoint.sh` writes this MCP server to `${HERMES_HOME}/config.yaml`
using the supported Hermes remote MCP config shape:

```yaml
mcp_servers:
  tp_knowledge_test:
    url: "https://test-mcp-bridge-germes-knowledge.astrogeoagent.ru/mcp"
    headers:
      Authorization: "Bearer ${TP_KNOWLEDGE_MCP_TOKEN}"
    tools:
      include: ["knowledge_answer_context"]
      resources: false
      prompts: false
```

The token remains an environment variable and is not written as a literal secret
to Git. The entrypoint does not print the token.

## Test edge protection

Prefer Coolify or Traefik Basic Auth on the test domain so every route is
protected before traffic reaches Hermes.

Recommended test setup:

```env
HERMES_DASHBOARD=0
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=9119
API_SERVER_KEY=<test API key>
HERMES_EDGE_BASIC_AUTH_ENABLED=false
```

This wrapper does not run an in-container Basic Auth proxy. Keep domain
protection in Coolify/Traefik, and keep API access protected with
`API_SERVER_KEY`. Do not reuse production credentials.

## Source routing policy

Routing between `GBrain`, `tp_knowledge`, and `Linear` is documented in:

- [docs/HERMES_SOURCE_ROUTING_POLICY.md](/Users/dbashkirtsev/Desktop/cline-sandbox/tp_Hermes%20Agent/docs/HERMES_SOURCE_ROUTING_POLICY.md)

Recommended deployment approach:

- add the routing block from that document into Hermes system instructions or SOUL prompt;
- if runtime-level prompt customization is not available, inject the same block from the caller that sends `messages` to Hermes.
