# Transit release configuration

Use the same checked source for test and production. Never set the production
deployment environment to `test` to enable a feature.

On the economy gateway, model selection requires
`HERMES_RUNTIME_TIER=economy`, `HERMES_FIXED_MODEL_PROVIDER=openrouter` and
`HERMES_DIALOGUE_MODEL_CHOICE_ENABLED=true`. Existing certified profiles,
provider identities and tokenizer evidence are unchanged by this release.

Terra requires `HERMES_TERRA_PERIODS_ENABLED=true`. Iskra Plus requires all of:

- `HERMES_DEEPSEEK_FLASH_PLUS_ENABLED=true`;
- `HERMES_DEEPSEEK_FLASH_PLUS_BACKEND_CAPABILITY=true`;
- a positive `HERMES_DEEPSEEK_FLASH_PLUS_MAX_CHARGE_KOPECKS`;
- the explicit `HERMES_DEEPSEEK_FLASH_PLUS_PRICE_POLICY_VERSION`.

Both model gates accept only the known `test` and `production` environments;
verified natal-plus-transit context, certificate and durable-request checks
remain mandatory. Enabling a flag does not admit a natal-only Terra/Plus call.

Measured attempt callbacks use a trusted endpoint for the current environment:

- test: `https://test.astrogeoagent.ru/api/internal/hermes/attempt-reserve`;
- production: `https://astrotochka.ru/api/internal/hermes/attempt-reserve`.

`DEPLOYMENT_ENVIRONMENT` is required when a measured descriptor is present.
Optional `HERMES_ATTEMPT_RESERVE_CALLBACK_URL` must equal the endpoint above.
Copied cross-environment URLs, credentials/query/fragment additions, arbitrary
hosts and redirects are rejected. Historical requests without measured control
retain their existing path. Request grants and price-policy snapshots are not
rewritten by a restart or a deployment.

Wallet markup is owned by Cabinet. The agreed production formula remains
100% + 1 AstroBall. Do not copy test wallets, billing rows, model credentials
or journals into production. Keep each deployment's existing `/opt/data` volume.
Back up journals consistently with SQLite WAL before a release; do not copy
only the main database file while a writer is active.

This document authorizes no environment changes or deployment. Apply the
release-specific approved configuration and verify its runtime health,
accounting persistence and exact source evidence.
