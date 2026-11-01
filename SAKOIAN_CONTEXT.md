# Test Sakoian narration contract

`tp_analysis_context` is separate from `tp_reading_context` and managed policies.
Only the authenticated trusted operator bridge may submit it; this is not a
public client authorization feature. Core → tp-astro-analysis runs on the
private SSH-only analysis service before this gateway is called.

Exact pinned Knowledge family `sakoian_transit_analysis_core` 0.3.0 and executor
package hash are in `hermes_sakoian_manifest.json`. The vendored result schema
matches tp-astro-analysis 0.7.0. The validator checks identity, five factor
ordering, coverage, fact/derivation links, Core release and immutable pins.
Hashes are integrity evidence, not digital signatures or user authorization.

Narration uses no tools, history, shared session, memory or competing method.
A durable Idempotency-Key matching request_id is required. The response includes
`tp_analysis_execution`; callers must verify this receipt before accepting text.
`present`, `absent`, `unknown`, and `not_applicable` remain distinct.
The single context covers five factors of one selected transit, not the full book.

The aggregate candidate adds a separate `tp-astro-analysis.hermes-aggregate.v2`
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

The live aggregate check used one generation but exposed narration errors:
planet signs were mixed and not_applicable was described as natal reinforcement.
The model prompt now uses named natal/transit planet tables and direct event
records rather than opaque fact-hash lookups. Audit projection and all pins are
unchanged. Same-planet aspects are called same_planet_transit in narration data;
the persisted legacy activation code is not exposed to the model. This is a
readability correction, not a claim that prompt instructions prove prose accuracy.
A new live quality check needs a separate explicitly authorized generation.


## Grounded aggregate narration

The v2 aggregate contains an executor-generated Russian narrative catalogue,
bound by the aggregate digest and exact package pin. Hermes recomputes it
before dispatch. The model returns only a strict JSON ordering plan containing
all group IDs and all event IDs exactly once, with the exact catalogue hash.
Unknown IDs, omissions, duplicates, free-text fields, invalid JSON or truncation
fail closed. There is no semantic regex classifier for arbitrary prose.

The gateway renders verified facts, method qualities and limitations itself.
`tp_analysis_validation` records the accepted plan and rendered text digest;
the executor client independently renders the same plan and requires exact
text equality. Failed provider text is suppressed; billing evidence remains
recorded and durable replay must not generate another attempt. No silent
fallback to a default plan. Single-aspect narration remains the older contract.

The source of facts and derivations remains the authenticated trusted operator
bridge and pinned executor, not a signature or a proof of astrological truth.
This stage provides a controlled account of existing method facts and qualities;
it does not add a new life-interpretation dictionary or validate arbitrary prose.
New live paid model calls require a separate explicit grant. Automated runtime
checks use a fake SDK and an isolated real temporary accounting journal.

## Private rejection diagnostics

Rejected aggregate plans are captured before their text is cleared. The record
contains the exact received text, its byte count and SHA-256, the request and
generation IDs, accounting payload hash, expected plan, completion metadata,
and a structured rejection reason. JSON failures include line/column/offset;
contract failures identify the field and expected/actual values or missing,
unknown and repeated IDs. This second-pass diagnosis cannot accept a plan.

The private SQLite sidecar is below the accounting journal's durable parent,
in `.hermes-sakoian-diagnostics/` (0700), with one 0600 database per journal.
It uses DELETE journaling and synchronous FULL. No migration of billing tables
or new secret is required. Existing accounting generation evidence must match
before a record is saved. A conflicting second capture cannot overwrite it.
The maximum retained raw text is 1 MiB; larger responses are explicitly marked
truncated, with the full original digest and byte count still recorded.

On rejection, `tp_analysis_validation` adds `reason_code`, `reason_path`,
`diagnostic_status` and, only when saved, `diagnostic_id`. Arbitrary model text,
expected/actual values and private details do not enter the HTTP response or
billing result. A failed sidecar write reports `diagnostic_status=unavailable`
and a safe error code; the rejection and generation accounting remain durable.
The two journals are separate transactions: this visible failure is deliberate,
not a claim of atomic capture with billing. There is no automatic paid retry.

An operator can read a record over the existing protected server access using
`agent.sakoian_diagnostics.read_rejection(request_key)`. The default returns safe
metadata. Explicit `include_plan=True` returns the private raw plan, exact reason,
completion metadata and expected plan. Do not copy this output into public logs,
issues or shared reports. There is no HTTP diagnostics endpoint or auto-deletion.

Captures apply to new rejected requests. Old failures whose text was discarded
cannot be reconstructed; replay of those requests never launches a new model
call. Valid plans and the legacy single-aspect route keep their existing contract.
