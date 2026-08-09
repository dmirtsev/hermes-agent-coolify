# Hermes durable accounting V1

Date: 2026-08-09

## Cabinet request contract

For each billable non-streaming `POST /v1/chat/completions`, Cabinet creates
one globally unique stable `Idempotency-Key` and uses that same value as its
reservation/request identity. A retry must send the same logical JSON payload
and the same key. A genuinely new user request must use a new key.

Hermes returns:

- `200` and the stored completion for a completed replay, with
  `X-Hermes-Idempotency-Replayed: true`;
- `409 idempotency_payload_conflict` for the same key/different payload;
- `409 idempotency_in_flight` while the original execution is running;
- `409 idempotency_unresolved` after a failed/uncertain execution;
- `503` if the durable journal cannot be claimed safely.

The first claim is persisted before OpenRouter dispatch. Exact generation
evidence is appended while the request runs, and the result plus accounting is
persisted before a successful response.

The durable request key is also bound to the request-owned Hermes agent and is
passed explicitly to evidence writes. This is required because provider and
stream workers do not inherit Python context variables. The binding restores
prior agent state after the request and serializes accidental concurrent reuse
of one agent, preventing evidence from one Cabinet request entering another.

## Storage and protection

Configure `HERMES_ACCOUNTING_JOURNAL_PATH`; the default is
`/opt/data/hermes-accounting.sqlite3`. Each Hermes tier needs its own
persistent `/opt/data` volume. Never share this SQLite file between tier
containers.

Internal reads/reconciliation require
`Authorization: Bearer $HERMES_ACCOUNTING_INTERNAL_TOKEN`. If the dedicated
token is not configured, Hermes falls back to `API_SERVER_KEY`. The safe view
excludes prompts, assistant results, and secrets.

## Reconciliation

`POST /internal/accounting/{request-key}/reconcile` performs an authenticated
OpenRouter lookup for every known generation whose cost is pending. Only
OpenRouter's exact `total_cost`, native token buckets, model, and provider are
persisted. Multiple generations are summed exactly before one micro-USD
rounding. Transient and HTTP errors stay pending and can be retried; unknown
generation IDs never become zero-cost or estimated charges.

`POST /internal/accounting/{request-key}/seal-not-dispatched` accepts the
canonical request `payload_sha256`. It atomically creates a terminal fence only
when Hermes has no receipt for that key. A later client retry is then rejected;
if Hermes already claimed the key, the endpoint returns its existing durable
state and never reports `not_dispatched`. Cabinet may release a wallet reserve
only after this authenticated terminal evidence.

## Explicit non-goals

- no Cabinet wallet mutation inside Hermes;
- no catalog-price estimate accepted as actual spend;
- no durable idempotency for streaming or `/v1/responses` in V1;
- no automatic redispatch of uncertain executions.
