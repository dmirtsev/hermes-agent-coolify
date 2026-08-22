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

The guard injects a closed system layer requiring Hermes to distinguish
calculation facts, method interpretation and personal hypotheses. It forbids
using MCP/RAG or general model knowledge to expand the astrology package.
The answer receives four explicit sections: calculation fact, selected-method
interpretation, personal hypothesis, and limitations/next step. Cabinet's
`show_method` and `show_sources` choices are converted into explicit closed
prompt instructions. Method provenance remains in the package and therefore
in Cabinet evidence even when the user elects not to display sources.

On a successful non-streaming response the gateway adds
`tp_method_execution` with exact family, version, content hash, retrieval trace,
mixing policy and prompt-contract version. Cabinet must match this receipt
before completing the turn. The receipt is deterministic on a durable replay.

The implementation is copied to `/opt/hermes/agent/versioned_methods.py` and
patched into the digest-pinned v0.16.0 API server. Any upstream source drift
causes the image build to fail at the reviewed replacement anchors.
