# Hermes OpenRouter accounting preflight

Date: 2026-08-09

## Finding for the pinned image

The exact pinned Hermes v0.16.0 image reports aggregate token counts, but its
OpenAI-compatible API response does not expose authoritative OpenRouter cost,
the upstream generation id, or a provider-reported executed model.

Evidence in the pinned source:

- `agent/transports/types.py` normalizes usage to token fields only;
- `agent/transports/chat_completions.py::normalize_response` discards the
  upstream response id/model and does not copy `usage.cost`;
- `agent/conversation_loop.py` calculates `estimate_usage_cost()` from model
  catalog pricing and stores it as `session_estimated_cost_usd`;
- the same function explicitly labels OpenRouter cost as an estimate until
  reconciliation;
- `agent/turn_finalizer.py` returns configured `agent.model`/`agent.provider`
  and estimated cost, but no upstream generation id;
- `gateway/platforms/api_server.py::_run_agent` keeps only aggregate token
  counts, and the public completion response uses the caller's cosmetic
  request `model` value.

Therefore current Hermes output must be represented as
`cost.status=cost_unavailable`; catalog-derived values may be used for a
pre-request reservation only and must not be posted as final actual spend.
The machine-readable target shape is `deploy/hermes-tiers/usage-result.schema.json`.

## Exact implementation seam for a later code stage

1. For OpenRouter calls, request usage accounting and retain every upstream
   response id because one Hermes turn may perform several LLM calls around
   tools.
2. Extend normalized response/provider data with provider-reported model,
   generation id, token buckets, and actual cost when present. Never source
   these fields from the incoming API request.
3. Accumulate per-generation records on the agent. Mark a turn `actual` only
   when all successful billable calls have authoritative cost; otherwise emit
   `cost_unavailable` and queue reconciliation.
4. Add a server-side OpenRouter generation lookup worker for ids whose cost is
   not immediately returned. It must be idempotent and authenticated with a
   backend-only key.
5. Return the accounting extension from non-streaming, streaming, and run API
   paths. Cabinet finalizes the wallet reservation only from an idempotent,
   fully reconciled `actual` result; pending/unavailable results remain held or
   enter a bounded reconciliation policy.
6. Test multi-call tool turns, retries, partial output, duplicate delivery,
   missing ids, lookup delay/failure, and the difference between configured
   and provider-reported model identity.

This seam is intentionally not patched in the deployment-contract change: it
touches provider transport, aggregation, public API, reconciliation, and the
financial ledger, so it needs a separate reviewed implementation stage.
