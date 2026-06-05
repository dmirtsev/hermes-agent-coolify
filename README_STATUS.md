# Hermes + GBrain deployment status

Date: 2026-06-05

## Current status

The base deployment is working.

- Hermes Agent is deployed through Coolify using a thin wrapper repository.
- Hermes uses the official Docker image: `nousresearch/hermes-agent:latest`.
- Hermes starts in gateway mode through `CMD ["gateway", "run"]`.
- Hermes dashboard is available at `https://hermes.astrogeoagent.ru`.
- GBrain is deployed separately and available as an HTTP MCP server at `https://gbrain.astrogeoagent.ru/mcp`.
- Hermes can connect to GBrain through MCP.
- `hermes mcp test gbrain` successfully connects and discovers 81 tools.
- After `/reload-mcp now`, the active Hermes session sees `mcp_gbrain_*` tools.
- Smoke test completed: Hermes can use GBrain tools through MCP.

## Model

Hermes is currently configured to use OpenRouter with:

```yaml
model:
  provider: openrouter
  default: deepseek/deepseek-v4-flash
  base_url: https://openrouter.ai/api/v1
```

The OpenRouter key is stored in Hermes runtime data and must not be committed.

## Runtime domains

```text
Hermes dashboard: https://hermes.astrogeoagent.ru
GBrain MCP:       https://gbrain.astrogeoagent.ru/mcp
```

## Persistent data

Hermes persistent data is mounted at:

```text
/opt/data
```

This contains runtime config, `.env`, sessions, skills, logs and other mutable state.

## Important runtime commands

Check Hermes:

```bash
curl -I https://hermes.astrogeoagent.ru
```

Check GBrain MCP from inside the GBrain container:

```bash
bun run src/cli.ts auth test https://gbrain.astrogeoagent.ru/mcp --token <GBRAIN_TOKEN>
```

Check MCP from Hermes:

```bash
/opt/hermes/.venv/bin/hermes mcp list
/opt/hermes/.venv/bin/hermes mcp test gbrain
```

Reload MCP inside a Hermes session:

```text
/reload-mcp now
```

## Security notes

- No secrets must be committed to GitHub.
- GBrain token was used for testing and should be rotated before production.
- OpenRouter key should remain only in runtime env or `/opt/data/.env`.
- Do not ask Hermes to read or print `config.yaml`, `.env`, `Authorization`, `OPENROUTER_API_KEY`, or tokens.

## Next tasks

1. Define Hermes role / SOUL for the AstroGeo / AstroFest context.
2. Define first real memory scenario: user asks an astrology question, Hermes searches GBrain, answers using retrieved context.
3. Add access policy layer for future multi-user use: user id, role, tariff, allowed projects, allowed knowledge bases, allowed objects.
4. Decide where custom AstroFest / Hermes policy code will live. Do not put private business logic into this public wrapper repository.
5. Rotate the temporary GBrain token before any production or external access.
