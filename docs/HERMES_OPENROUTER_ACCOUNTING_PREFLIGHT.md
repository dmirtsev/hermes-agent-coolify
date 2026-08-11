# Hermes OpenRouter accounting seam

Date: 2026-08-09

## Pinned source finding

The exact Hermes v0.16.0 image originally exposed only aggregate token counts.
Its normalizer discarded upstream response identity and `usage.cost`, its
conversation loop retained only catalog estimates, and the public API echoed
the caller's cosmetic `model` field.  Those values were not sufficient for a
wallet debit.

This wrapper now applies a fail-closed build-time patch to the pinned source.
If any reviewed source anchor changes, the Docker build exits non-zero.

## Implemented evidence path

For every OpenRouter attempt the patched runtime now:

1. requests `X-OpenRouter-Metadata: enabled`, while preserving other
   provider headers;
2. retains the upstream generation ID from the response body or
   `X-Generation-Id` streaming header;
3. retains the provider-reported executed model and selected upstream
   provider when OpenRouter supplies them;
4. retains native input/output/cache/reasoning token buckets and
   OpenRouter's response `usage.cost`;
5. records every returned attempt before Hermes decides whether to retry it;
6. marks a dispatched exception as unresolved, because a later successful
   retry must not hide a possibly billable earlier attempt;
7. deduplicates repeated delivery of the same generation ID and fails closed
   if duplicate evidence conflicts;
8. marks every failed hidden streaming retry as unresolved before retrying;
9. records direct iteration-limit summary calls, while any auxiliary-client
   path that cannot yet prove every internal retry is explicitly unresolved;
10. preserves already-recorded OpenRouter evidence if Hermes mutates its
    current provider/model during fallback;
11. aggregates all model calls in a tool-using turn;
12. returns the result as top-level `hermes_accounting` on non-streaming
   `/v1/chat/completions` and `/v1/responses` responses, including the
   chat-completions hard-failure envelope.

The configured model in this extension comes only from the server-side Hermes
agent.  The provider-reported model comes only from the upstream response.
The incoming OpenAI-compatible request `model` is never passed to the
accounting builder.

OpenRouter now documents that detailed usage, including cost, is returned
automatically in each response; the older `usage.include` option is deprecated:
<https://openrouter.ai/docs/cookbook/administration/usage-accounting>.
OpenRouter also documents asynchronous lookup by generation ID:
<https://openrouter.ai/docs/api/api-reference/generations/get-generation>.

## Financial status rules

The schema is `deploy/hermes-tiers/usage-result.schema.json` (version 2).

- `actual`: every recorded generation has an authoritative generation ID and
  provider-reported `usage.cost`; `amount_usd` is the exact decimal sum and
  canonical `amount_micro_usd` is that request total rounded half-up once, and
  `fully_reconciled=true`.
- `pending`: every generation has an ID, but one or more costs are absent;
  the unique IDs are returned for a backend reconciliation worker.
- `cost_unavailable`: at least one dispatched/returned attempt lacks a usable
  generation ID, duplicate evidence conflicts, or no billable generation
  evidence exists. No estimate is emitted as actual spend.

Catalog pricing remains valid only for a pre-request reservation.  Cabinet
must finalize a wallet debit only from a schema-valid `actual` result.  It must
keep the reservation pending (or apply its bounded timeout policy) for
`pending`/`cost_unavailable`.

`request_id` is a Hermes-generated billing-event identity. It is created
inside the cached agent result, so replaying the same `Idempotency-Key` keeps
the same accounting ID even though the compatibility API may generate a new
response ID. Cabinet must additionally enforce uniqueness of every upstream
generation ID; either identity repeating makes the ledger operation a no-op.

## Durable V1 boundary

Billable non-streaming `/v1/chat/completions` requests now require Cabinet to
send a stable `Idempotency-Key`. Hermes stores the key, canonical payload hash,
attempt evidence, immutable result, usage, and accounting in SQLite under its
unique `/opt/data` volume. The claim is committed before provider dispatch and
completion is committed before the successful HTTP response. A restart can
therefore replay a completed result without another OpenRouter request.

The same key with a different payload, an in-flight key, or a key whose prior
execution failed unresolved returns `409`; it is never silently redispatched.
This is deliberately fail-closed: operators must reconcile or investigate an
unresolved attempt rather than risk a second charge.

Protected internal endpoints are keyed by the Cabinet idempotency key:

- `GET /internal/accounting/{request-key}` reads the safe durable view;
- `POST /internal/accounting/{request-key}/reconcile` looks up every pending
  known generation with OpenRouter `GET /api/v1/generation?id=...`.

They use `HERMES_ACCOUNTING_INTERNAL_TOKEN`, falling back to `API_SERVER_KEY`
only when the dedicated token is absent. Responses contain no prompt, model
response, OpenRouter key, or other secret. Lookup `404`, `429`, network, and
`5xx` failures remain `pending`; a missing generation ID requires manual
review. No catalog estimate becomes a debit.

Durable V1 intentionally covers non-streaming Chat Completions only. Streaming
and `/v1/responses` retain their existing immediate accounting behavior and
must not be used as the Cabinet wallet-finalization path yet. Hermes does not
mutate the Cabinet ledger; Cabinet consumes the journal/accounting contract in
its own atomic wallet transaction.

## Verification

Mocked tests cover:

- multiple OpenRouter generations in a tool turn;
- returned retry generations and dispatched attempts with no response;
- failed hidden streaming attempt followed by a successful retry;
- direct summary calls and fail-closed auxiliary calls;
- stable billing identity on Idempotency-Key replay;
- persistent replay after restart, same-key/different-payload conflict, and
  in-flight/unresolved fail-closed behavior;
- pending generation reconciliation including multi-generation sums and
  `404`/`429`/`5xx` retention;
- evidence retention across mutable provider fallback;
- exact-sum request rounding for sub-micro generation charges;
- missing cost and missing generation ID;
- duplicate generation delivery;
- non-OpenRouter compatibility;
- separation of the caller's cosmetic model from configured/executed models;
- strict output validation and rejection of catalog estimates as actual;
- the patched transport and non-streaming API response inside the built image.

No test uses a real provider key or network call.
