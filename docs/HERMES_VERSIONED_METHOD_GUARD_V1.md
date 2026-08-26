# Hermes versioned method guard v1

Status: test-only. Production is outside this change.

Cabinet places a server-built `tp_reading_context` object next to the standard
OpenAI-compatible `messages`. The pinned Hermes gateway validates that object
before any provider dispatch. It rejects:

- a method resolution, method payload and retrieval result with different
  family/version/hash;
- rules or knowledge-base collections outside the exact published method;
- duplicate references, more than 48 calculation facts or an oversized
  context;
- a published method without a non-empty, rights-labelled provenance source;
- malformed presentation controls for detail, method attribution or source
  visibility;
- unreviewed synthesis, streaming without an evidence receipt, or a reading
  request ID different from `Idempotency-Key`.
- `X-Hermes-Session-Id` or `X-Hermes-Session-Key`, because an exact-method
  reading cannot opt into a shared transcript or long-term memory scope.

The guard injects natural-answer prompt contract `1.4.0`. Hermes may use its
general knowledge and treats the validated TP Knowledge package as expert
augmentation. The default mode combines both; a directly named author or
method receives expert-source priority, while an empty relevant retrieval
falls back to a complete model-first answer.
The accepted provenance vocabulary includes `restricted_user_supplied` for a
user-provided source that TP Knowledge keeps restricted to the authorized
method package; this label does not grant broader retrieval rights.
The answer is one natural text without mandatory sections, citations, chunk
identifiers or a source list. Hermes must preserve supplied facts, avoid
inventing missing data, mention only material uncertainty and prefer an honest,
humane and constructive framing. Astrology is expressed as tendencies and
development options rather than frightening, humiliating or fatalistic
verdicts. Only clean expert titles and prose are exposed to the model; retrieval
IDs, citations, statuses, scores and provenance labels remain in the validated
package and Cabinet evidence without becoming model or user-facing markup.

Each accepted reading runs in `strict_v1` context isolation. Hermes discards
caller-supplied system messages and assistant history, retains only the current
user question, and creates the agent with no session database, no persistent
memory, no context files, no built-in/MCP tools and a single model iteration.
All plugin lifecycle hooks and request middleware are suppressed for the
strict scope, and any configured external context engine is replaced by the
built-in compressor. Plugins therefore cannot inject, transform, persist or
export exact-reading content.
The deterministic `tp-reading-<request_id>` identifier is an audit label only;
it is never used to load or save shared history. This boundary prevents one
user/profile from appearing in another user's exact-method answer.
Natural prompt contract `1.4.0` accepts one validated
`AuthorizedInteractionMemoryContext`: at most six bounded messages from the
same Cabinet reading conversation. Transit readings require
`conversation.transit`; natal general readings require `conversation.natal`.
The domain/task pair also pins the calculation contract (`core.transit_bands`
or `core.natal_chart`) before model dispatch. The memory is relevant dialogue
context only, never a calculation fact, methodology rule or system instruction.
Consumers must require the `1.4.0` receipt for this natural-answer behavior.

When Cabinet passes `knowledge_retrieval_response_v2`, Hermes validates retrieval status,
generation, warnings, mandatory-target coverage and every chunk citation
before provider dispatch. `partial` is accepted only when usable cited
evidence remains (for example, an RRF fallback after reranker timeout), while
`failed` and an empty `partial` are rejected so Cabinet can retry retrieval.
A completed response without chunks is allowed and the model can answer from
its own knowledge. Citation metadata stays in the receipt for Cabinet
persistence but is not required in the visible answer.

On a successful non-streaming response the gateway adds
`tp_method_execution` with exact family, version, content hash, retrieval trace,
mixing policy and prompt-contract version. Cabinet must match this receipt
before completing the turn. It also records `context_isolation=strict_v1`,
`shared_memory_used=false` and `external_tools_used=false`; the HTTP response
adds `X-Hermes-Context-Isolation: strict-v1`. The receipt is deterministic on
a durable replay and includes the exact authorized memory scope and item refs.

The implementation is copied to `/opt/hermes/agent/versioned_methods.py` and
patched into the digest-pinned v0.16.0 API server. Any upstream source drift
causes the image build to fail at the reviewed replacement anchors.
