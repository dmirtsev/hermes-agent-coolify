# Three isolated Hermes runtimes

Hermes v0.16.0 does not route the upstream model from the request `model`
field. Sprint 1 therefore uses three separate Coolify applications:
`economy`, `balanced`, and `strong`. Each application has one fixed
OpenRouter `model.default` and Cabinet selects an application endpoint from a
server-side allowlist.

## Isolation requirements

For every tier create a distinct Coolify application with:

- branch `test` during test validation;
- one unique HTTPS domain and port `9119`;
- one unique persistent volume mounted at `/opt/data`;
- one unique `API_SERVER_KEY` shared only with the Cabinet backend;
- one runtime-scoped `OPENROUTER_API_KEY` created under the owner's OpenRouter
  account (all keys may bill the same owner account, but key identity and
  revocation stay isolated);
- one SQLite accounting journal at
  `/opt/data/hermes-accounting.sqlite3` on that runtime's unique volume;
- one `HERMES_ACCOUNTING_INTERNAL_TOKEN` shared only with the Cabinet backend
  (or an intentional decision to reuse `API_SERVER_KEY`);
- the runtime-only variables from `runtime.env.example`;
- no browser-visible provider key, API key, or arbitrary endpoint/model input.

Do not attach one volume to two applications. Do not copy a production volume
or secret into test. Keep **Available at Buildtime** disabled for all secrets
and runtime contract values.

The wrapper atomically applies only these fields to that runtime's
`/opt/data/config.yaml`:

```yaml
model:
  provider: openrouter
  default: <fixed OpenRouter model id>
  base_url: https://openrouter.ai/api/v1
  max_tokens: 4096
```

Before the API starts, release evidence reads the resulting YAML back and
compares it with the contract variables. Any mismatch fails closed. A healthy
runtime reports the verified non-secret values in `release.routing`.

## Operator sequence for test

1. Copy `manifest.test.example.json` to an untracked deployment manifest.
2. Replace the three `change_me/...` model ids with the administrator-approved
   OpenRouter ids and replace `__SOURCE_COMMIT__` with the 40-character test
   commit.
3. Validate it with `scripts/validate_hermes_tiers_manifest.py --deployment-ready`.
4. Create/configure all three Coolify applications exactly as the manifest
   describes. Deploy only the `test` branch.
5. Run `scripts/smoke_hermes_tiers.py`. It verifies HTTP health, release commit,
   environment, tier, runtime identity, provider, actual fixed model, and token
   cap for all endpoints.

The OpenAI-compatible response `model` field is deliberately not accepted as
proof of routing. The smoke check trusts only startup evidence derived from
the runtime's actual persisted `config.yaml`.

## Production preparation (not a deploy)

`manifest.production.example.json` and `runtime.production.env.example` are
intentionally incomplete templates. They contain `.invalid` endpoint origins,
placeholder models and no credentials, so they cannot be used with
`--deployment-ready`. This avoids copying a test application or test secret
into production by accident.

After an explicit production release decision, the release owner must create a
private, untracked deployment manifest with:

1. the exact approved wrapper SHA and three approved fixed model IDs;
2. three production-only HTTPS origins and runtime IDs;
3. three unique `/opt/data` volumes and never a copied test/legacy volume;
4. independent `API_SERVER_KEY`, OpenRouter and accounting tokens for every
   runtime, available at runtime only;
5. the production `TP_KNOWLEDGE_MCP_*` values from the separate production
   bridge configuration;
6. an internal Coolify deployment record and `/health/detailed` smoke for every
   tier, followed by a protected replay/accounting check from Cabinet.

The public production endpoint is not a health assertion from the VPN route.
Use Coolify/internal health evidence for the production wave.

## Usage and cost contract

The wrapper now retains per-generation OpenRouter identity, native token
buckets, and `usage.cost`, then returns schema-version-2
`hermes_accounting` on non-streaming Chat Completions and Responses API calls.
Multi-call tool turns are aggregated and generation IDs are deduplicated.

Cabinet may finalize a wallet debit only when the machine-readable
`usage-result.schema.json` contract says `cost.status=actual` and
`fully_reconciled=true`. `pending` carries generation IDs for the authenticated
server-side OpenRouter lookup. `POST
/internal/accounting/{idempotency-key}/reconcile` performs that lookup; `GET
/internal/accounting/{idempotency-key}` returns the current safe journal view.
Both require a bearer token and never return prompts, responses, or provider
secrets. `cost_unavailable` and catalog estimates must never be posted as
actual spend. See
`docs/HERMES_OPENROUTER_ACCOUNTING_PREFLIGHT.md` for the complete evidence and
failure rules.

For billable non-streaming Chat Completions, Cabinet must send a stable unique
`Idempotency-Key`. Hermes claims it durably before provider dispatch, stores
the exact result/accounting before replying, and replays it after restart.
Reusing a key with a different payload, while it is in flight, or after an
unresolved failure returns `409` and never dispatches OpenRouter again. This
V1 does not apply durable idempotency to streaming or the Responses API.
