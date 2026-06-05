# Hermes Agent for Coolify

This repository is a tiny Coolify wrapper around the official Hermes Agent Docker image.

It does not copy Hermes source code.

```dockerfile
FROM nousresearch/hermes-agent:latest
CMD ["gateway", "run"]
```

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

## GBrain MCP test config

After Hermes starts, connect GBrain as a remote HTTP MCP server in `/opt/data/config.yaml` or through Hermes MCP commands:

```yaml
mcp_servers:
  gbrain:
    url: "https://gbrain.astrogeoagent.ru/mcp"
    headers:
      Authorization: "Bearer CHANGE_ME_GBRAIN_TOKEN"
```

The current GBrain token used during testing must be revoked and replaced before production.
