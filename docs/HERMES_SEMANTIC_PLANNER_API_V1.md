# Hermes Astrological Semantic Planner API v1

## Purpose

`POST /v1/astrology/semantic-plan` converts a human question and a trusted
context card into a compact, source-independent astrological semantic brief.

The endpoint is an isolated test-stage capability. It does not call TP
Knowledge, choose sources, use MCP tools or generate a user-facing answer.
Cabinet does not call it yet.

## Boundary

The planner owns:

- preserving the original intent;
- exposing the human meanings in the question;
- mapping those meanings to astrological symbolism;
- selecting one to four prioritized foci;
- recording constraints and material ambiguity.

The output is deliberately compact: at most four foci, four symbols per focus,
four constraints and two ambiguities. A rationale explains relevance of the
symbolism; it must not become a chart interpretation.

It does not own:

- chart calculation or correction;
- source, author, method or knowledge-base selection;
- retrieval, ranking, coverage or provenance;
- final interpretation;
- dialog persistence or caching.

Only `natal` executes in v1. `transit`, `predictive` and `earth_points` are
recognized contract values and fail with `unsupported_context` until their
context cards and acceptance suites are approved.

## Request

The endpoint uses the regular Hermes Bearer API authorization and requires an
`Idempotency-Key` header. The body is a strict JSON object:

```json
{
  "schema_version": 1,
  "request_id": "planner-request-1",
  "original_question": "В чём моя сила?",
  "context_card": {
    "context_type": "natal",
    "scenario": "general_reading",
    "facts": [],
    "allowed_concepts": ["планеты", "дома", "аспекты"],
    "forbidden_inferences": [
      "Не объявлять отсутствующий показатель фактом"
    ]
  },
  "dialog_context": []
}
```

Callers cannot select a model, temperature, tools or output limit. The runtime
uses its fixed model and bounded planner settings.

## Successful response

The response contains the strict brief, token usage and the same authoritative
OpenRouter accounting evidence used by other direct Hermes completions:

```json
{
  "brief": {
    "schema_version": 1,
    "planner_version": "hermes.astrological-semantic.v1",
    "request_id": "planner-request-1",
    "original_intent": "Понять устойчивые ресурсы личности",
    "context_type": "natal",
    "focuses": [
      {
        "focus_id": "f1",
        "human_meaning": "Основные устойчивые ресурсы",
        "astrological_symbols": [
          "доминирующие комплексы",
          "повторяющиеся связи"
        ],
        "rationale": "Повторяемость помогает выделить устойчивый ресурс.",
        "priority": 1
      }
    ],
    "constraints": ["Не пересказывать всю карту"],
    "ambiguities": []
  },
  "model": "runtime/fixed-model",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "hermes_accounting": {}
}
```

## Failure behavior

- invalid or unknown input fields: `400`;
- missing or invalid authentication: `401`;
- unsupported known context: `422`;
- model output outside the strict brief schema: `422`;
- reused idempotency key with another payload: `409`;
- unavailable accounting journal: `503`;
- provider/runtime execution failure: `502`.

No failure changes the existing Hermes answer path.
Schema errors return a bounded field-level diagnostic and finish reason. Raw
provider output is never returned.

## Evaluation gate

Run the five synthetic cases with:

```text
HERMES_API_KEY=<test secret> \
python3 scripts/evaluate_semantic_planner.py \
  --base-url <test Hermes URL>
```

Do not paste the API key into logs or reports.

The first test gate is:

- 15/15 schema-valid responses across three runs of the five cases;
- no source, author or retrieval selection in any brief;
- no invented chart fact;
- exact question normally produces one focus;
- broad question produces two to four non-duplicated foci;
- health case preserves a non-medical-causality constraint;
- median manual score at least 4/5 for intent, relevance, grounding,
  compactness and context fit;
- median latency at most 8 seconds and p95 at most 15 seconds;
- actual cost/token evidence present for every dispatched call.

Passing this gate proves that the API produces usable briefs. It does not yet
prove that Hermes answers improve. That requires a later shadow comparison and
retrieval A/B test after a separate Cabinet and TP Knowledge integration.
