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

The guard injects a closed system layer requiring Hermes to distinguish
calculation facts, method interpretation and personal hypotheses. It forbids
using MCP/RAG or general model knowledge to expand the astrology package.
The accepted provenance vocabulary includes `restricted_user_supplied` for a
user-provided source that TP Knowledge keeps restricted to the authorized
method package; this label does not grant broader retrieval rights.
The answer receives four explicit sections: calculation fact, selected-method
interpretation, personal hypothesis, and limitations/next step. Cabinet's
`show_method` and `show_sources` choices are converted into explicit closed
prompt instructions. Method provenance remains in the package and therefore
in Cabinet evidence even when the user elects not to display sources.

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
Prompt contract `1.2.0` additionally accepts one validated
`AuthorizedInteractionMemoryContext`: at most six bounded messages from the
same Cabinet reading conversation. Transit readings require
`conversation.transit`; natal general readings require `conversation.natal`.
The domain/task pair also pins the calculation contract (`core.transit_bands`
or `core.natal_chart`) before model dispatch. The memory is prompt data for the
personal hypothesis only, never a calculation fact, methodology rule or system
instruction. Consumers must reject `1.0.0` and `1.1.0` receipts because they
do not prove this complete boundary.

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
