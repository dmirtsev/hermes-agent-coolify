# Test Sakoian narration contract

`tp_analysis_context` is separate from `tp_reading_context` and managed policies.
Only the authenticated trusted operator bridge may submit it; this is not a
public client authorization feature. Core → tp-astro-analysis runs on the
private SSH-only analysis service before this gateway is called.

Exact pinned Knowledge family `sakoian_transit_analysis_core` 0.3.0 and executor
package hash are in `hermes_sakoian_manifest.json`. The vendored result schema
matches tp-astro-analysis 0.5.0. The validator checks identity, five factor
ordering, coverage, fact/derivation links, Core release and immutable pins.
Hashes are integrity evidence, not digital signatures or user authorization.

Narration uses no tools, history, shared session, memory or competing method.
A durable Idempotency-Key matching request_id is required. The response includes
`tp_analysis_execution`; callers must verify this receipt before accepting text.
`present`, `absent`, `unknown`, and `not_applicable` remain distinct.
Only five groups of one selected transit are in scope, not the full book.

This candidate starts from verified production 658a105 and explicitly preserves
already installed test 7557cf6c (periods/context budgets and accounting).
Production is not a delivery target. No credential or model configuration changes.

The live check exposed Hermes automatic continuation after token exhaustion.
Sakoian now uses one direct SDK call inside the existing durable accounting
scope: SDK retries disabled, fixed approved economy model, max_tokens=4096,
reasoning disabled, no provider fallback. A length/empty/tool response fails
closed; it is never silently continued. Other Hermes routes are unchanged.
