# Test Sakoian narration contract

`tp_analysis_context` is separate from `tp_reading_context` and managed policies.
Only the authenticated trusted operator bridge may submit it; this is not a
public client authorization feature. Core → tp-astro-analysis runs on the
private SSH-only analysis service before this gateway is called.

Exact pinned Knowledge family `sakoian_transit_analysis_core` 0.3.0 and executor
package hash are in `hermes_sakoian_manifest.json`. The vendored result schema
matches tp-astro-analysis 0.6.0. The validator checks identity, five factor
ordering, coverage, fact/derivation links, Core release and immutable pins.
Hashes are integrity evidence, not digital signatures or user authorization.

Narration uses no tools, history, shared session, memory or competing method.
A durable Idempotency-Key matching request_id is required. The response includes
`tp_analysis_execution`; callers must verify this receipt before accepting text.
`present`, `absent`, `unknown`, and `not_applicable` remain distinct.
The single context covers five factors of one selected transit, not the full book.

The aggregate candidate adds a separate `tp-astro-analysis.hermes-aggregate.v1`
context. It validates every included single-transit result with the existing
gate, requires two to 24 results with one shared identity and release pin, and
uses one bounded Hermes call. Grouping is deterministic and structural; the
model receives only the compact projection and reports one synthesis rather
than five repeated factor blocks. A 600 KB gateway-context limit and 60 KB complete system-prompt limit and the
existing fail-closed behavior apply. The original single-aspect context is
unchanged.

This candidate starts from verified production 658a105 and explicitly preserves
already installed test 291c084 (periods/context budgets, accounting and bounded narration).
Production is not a delivery target. No credential or model configuration changes.

The live check exposed Hermes automatic continuation after token exhaustion.
Sakoian now uses one direct SDK call inside the existing durable accounting
scope: SDK retries disabled, fixed approved economy model, max_tokens=4096,
reasoning disabled, no provider fallback. A length/empty/tool response fails
closed; it is never silently continued. Other Hermes routes are unchanged.
