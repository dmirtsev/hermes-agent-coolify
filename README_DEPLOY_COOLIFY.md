# Hermes Agent for Coolify

This repository is a tiny Coolify wrapper around the official Hermes Agent Docker image.

It does not copy Hermes source code.

The wrapper keeps Hermes' official s6 entrypoint and installs a small
`cont-init.d` hook that registers the configured TP Knowledge MCP server before
Hermes starts.

The wrapper also publishes non-secret release identity in `/health` and
`/health/detailed`, writes the same payload to `${HERMES_HOME}/release.json`,
and prints a compact release line at startup. This makes it possible to verify
that the running container matches the Git commit intended for deployment.

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

The wrapper starts the gateway through Hermes' built-in s6 supervisor. Its
initial state is `running` only on a fresh volume; later restarts preserve the
last saved state. Do not override the Docker command with `gateway run`: that
would start a second gateway alongside the supervisor and makes a redeploy
unhealthy.

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
TP_KNOWLEDGE_MCP_REPLACE_ALL=true
```

At startup, `/etc/cont-init.d/90-tp-knowledge-mcp` writes this MCP server to `${HERMES_HOME}/config.yaml`
using the supported Hermes remote MCP config shape:

```yaml
mcp_servers:
  tp_knowledge_test:
    url: "https://test-mcp-bridge-germes-knowledge.astrogeoagent.ru/mcp"
    headers:
      Authorization: "Bearer ${MCP_TP_KNOWLEDGE_TEST_API_KEY}"
    tools:
      include: ["knowledge_answer_context"]
      resources: false
      prompts: false
```

The token is copied from `TP_KNOWLEDGE_MCP_TOKEN` into Hermes' runtime `.env`
as `MCP_TP_KNOWLEDGE_TEST_API_KEY`, matching Hermes CLI's supported remote MCP
Bearer-token convention. It is not written to Git and is not printed.

For an isolated test contour, `TP_KNOWLEDGE_MCP_REPLACE_ALL=true` replaces the
`mcp_servers` block with only `tp_knowledge_test`, so stale production or
third-party MCP registrations from the persistent volume are not loaded.

## Production TP Knowledge MCP config

Production must use its own Coolify environment values and must not copy test
credentials:

```env
TP_KNOWLEDGE_MCP_ENABLED=true
TP_KNOWLEDGE_MCP_URL=https://mcp-bridge-germes-knowledge.astrogeoagent.ru/mcp
TP_KNOWLEDGE_MCP_TOKEN=<secret from the production MCP_BRIDGE_TOKEN>
TP_KNOWLEDGE_MCP_NAME=tp_knowledge
TP_KNOWLEDGE_MCP_REPLACE_ALL=false
API_SERVER_PORT=8642
```

The production-safe default is `TP_KNOWLEDGE_MCP_REPLACE_ALL=false`. Existing
MCP registrations in the persistent Hermes config are preserved and the
`tp_knowledge` entry is added or updated in place. `API_SERVER_PORT` must match
the port exposed by the production Coolify application; the wrapper preserves
the environment-specific value instead of forcing the test port.

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
HERMES_WRAPPER_COMMIT=$SOURCE_COMMIT
HERMES_DEPLOYMENT_ENVIRONMENT=test
HERMES_RELEASE_EVIDENCE_REQUIRED=true
```

This wrapper does not run an in-container Basic Auth proxy. Keep domain
protection in Coolify/Traefik, and keep API access protected with
`API_SERVER_KEY`. Do not reuse production credentials.

`HERMES_WRAPPER_COMMIT`, `HERMES_DEPLOYMENT_ENVIRONMENT`, and
`HERMES_RELEASE_EVIDENCE_REQUIRED` are runtime metadata, not secrets. Keep
them available at runtime. Coolify expands `$SOURCE_COMMIT` to the deployed Git
commit. As an alternative, enable **Include Source Commit in Build** so the
Dockerfile receives the `SOURCE_COMMIT` build argument; this invalidates the
image build cache on every commit.

Verify the deployed revision without printing credentials:

```bash
curl -fsS https://test-hermes.astrogeoagent.ru/health
```

The `release.wrapper_commit` value must equal the commit merged into `test`.

## Test LLM provider

Hermes can start and register MCP servers without an LLM provider, but it
cannot complete an agent response without one. Without a provider, the API
returns `No inference provider configured` before Hermes can produce the final
answer from MCP context.

For the test contour, set the provider only in the separate test Coolify
application, in **Environment Variables**. Do not add these values to Git and
do not reuse production keys.

For the legacy single-runtime smoke setup, set the key and select the model
with `hermes model` inside the test container:

```env
OPENROUTER_API_KEY=<test OpenRouter key>
```

Do not treat `HERMES_MODEL` as authoritative in the pinned version. New tiered
deployments use the explicit fixed-model contract below instead of a manual
model picker.

## Three fixed model tiers

For multi-user model choice, do not use `HERMES_MODEL` and do not trust the
OpenAI-compatible request `model` field. The safe Sprint 1 deployment contract
is three isolated Hermes applications (`economy`, `balanced`, `strong`), each
with a unique API token, persistent `/opt/data` volume, and fixed OpenRouter
model.

The machine-readable contract, runtime-only environment template, validation
command, and health smoke check are in
[`deploy/hermes-tiers/`](deploy/hermes-tiers/README.md). Model ids in the
example manifest are placeholders until the administrator publishes an
approved catalog selection.

For the pinned Hermes image, gateway/API-server requests do **not** select the
upstream model through the request's OpenAI-compatible `model` field. The field
is response metadata; agent creation continues to use `model.default` from
`${HERMES_HOME}/config.yaml`. See
[`docs/HERMES_MODEL_ROUTING_PREFLIGHT.md`](docs/HERMES_MODEL_ROUTING_PREFLIGHT.md).

Use the LLM provider only for end-to-end validation:

```text
Hermes test
-> MCP Bridge test
-> TP Knowledge test
-> luna
-> answer generated by Hermes from retrieved context
```

Infrastructure checks that do not require LLM tokens:

- Hermes `/health` returns `200`;
- Hermes API rejects missing `API_SERVER_KEY` with `401`;
- MCP Bridge rejects missing Bearer token with `401`;
- Hermes registers `tp_knowledge_test`;
- Hermes sees `knowledge_answer_context`;
- direct MCP call to `knowledge_answer_context` returns context from `luna`.

## OpenRouter accounting response

Non-streaming `/v1/chat/completions` and `/v1/responses` responses include a
top-level `hermes_accounting` object when the runtime is configured for
OpenRouter. Only `cost.status=actual` together with
`fully_reconciled=true` is final wallet evidence. `pending` returns upstream
generation IDs for backend reconciliation; `cost_unavailable` is explicitly
non-final. No OpenRouter secret is included in the payload.

The extension, micro-USD rounding rule, and failure semantics are documented
in [`docs/HERMES_OPENROUTER_ACCOUNTING_PREFLIGHT.md`](docs/HERMES_OPENROUTER_ACCOUNTING_PREFLIGHT.md).

## Backlog: token usage tuning

The first end-to-end test with `knowledge_answer_context` worked, but token
usage was high. Keep this as a tuning topic before using the contour for
regular traffic.

Observed during the test:

```text
Minimal ping through Hermes, without MCP:
prompt_tokens: ~17.9k

Knowledge question through Hermes and MCP:
prompt_tokens: ~54.6k
completion_tokens: ~0.5k
```

Likely contributors:

- Hermes has a large base agent context even for a minimal request. This likely
  includes system instructions, tool schemas, runtime instructions, skills, and
  platform/tool metadata from the Hermes image.
- Tool use normally requires two LLM passes: one pass to decide to call the MCP
  tool, then another pass to answer from the tool result.
- `knowledge_answer_context` returns a verbose JSON payload. Hermes needs the
  final context and compact source references, but the current payload also
  includes full chunk objects and metadata.

Where to investigate:

- Hermes runtime config in `/opt/data/config.yaml`: check whether the runtime
  supports reducing active skills, tools, platforms, system/SOUL prompt content,
  or forcing smaller tool-call arguments.
- This wrapper's `tp_knowledge_mcp_setup.sh`: if Hermes supports a prompt or
  policy field, inject test-only instructions to call `knowledge_answer_context`
  with smaller defaults such as `top_k=3`, `limit=3`, `max_chars=2000`.
- MCP Bridge / TP Knowledge implementation: add a compact response mode or
  reduce the default output for `knowledge_answer_context` so Hermes receives
  `context_text` plus short source references, not full chunk metadata.
- Upstream caller architecture: for a dedicated Knowledge QA endpoint, consider
  a single-pass flow where the caller invokes MCP first and then sends compact
  context to the LLM, instead of using the generic Hermes agent loop.

Questions to return to:

- Which Hermes config fields control the base system context and active bundled
  skills?
- Can test/prod Hermes run in a thin Knowledge-only mode?
- Should `knowledge_answer_context` expose a separate compact tool for LLM
  agents?
- What token budget should be enforced per Knowledge request?
- Should the bridge hard-cap `top_k`, `limit`, and `max_chars` regardless of
  model-selected arguments?

## Source routing policy

Routing between `GBrain`, `tp_knowledge`, and `Linear` is documented in:

- [docs/HERMES_SOURCE_ROUTING_POLICY.md](/Users/dbashkirtsev/Desktop/cline-sandbox/tp_Hermes%20Agent/docs/HERMES_SOURCE_ROUTING_POLICY.md)

Recommended deployment approach:

- add the routing block from that document into Hermes system instructions or SOUL prompt;
- if runtime-level prompt customization is not available, inject the same block from the caller that sends `messages` to Hermes.
