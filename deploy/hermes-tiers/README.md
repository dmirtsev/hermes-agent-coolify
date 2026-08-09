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

## Usage and cost contract

The wrapper now retains per-generation OpenRouter identity, native token
buckets, and `usage.cost`, then returns schema-version-2
`hermes_accounting` on non-streaming Chat Completions and Responses API calls.
Multi-call tool turns are aggregated and generation IDs are deduplicated.

Cabinet may finalize a wallet debit only when the machine-readable
`usage-result.schema.json` contract says `cost.status=actual` and
`fully_reconciled=true`. `pending` carries generation IDs for a future
server-side OpenRouter lookup. `cost_unavailable` and catalog estimates must
never be posted as actual spend. See
`docs/HERMES_OPENROUTER_ACCOUNTING_PREFLIGHT.md` for the complete evidence and
failure rules. Until the durable reconciliation worker exists, a gateway
disconnect or restart leaves the Cabinet reservation pending; absence of a
Hermes response is never evidence that OpenRouter charged zero.
