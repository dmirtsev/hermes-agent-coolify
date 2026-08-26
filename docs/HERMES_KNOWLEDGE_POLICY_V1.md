# Hermes Knowledge Policy V1

Cabinet may control external knowledge access for an individual Hermes request
without changing the runtime-wide MCP configuration.

## Request contract

```json
{
  "tp_knowledge_policy": {
    "schema_version": 1,
    "mode": "model_only"
  }
}
```

Supported modes:

- `model_only`: Hermes keeps the caller messages and normal conversation
  context, but the request-scoped agent receives no built-in or MCP toolsets.
  TP Knowledge therefore cannot be called even when it is registered globally.
- `knowledge_augmented`: the existing versioned reading/MCP path remains
  available. The caller remains responsible for sending the approved reading
  package and allowed knowledge bases.

An absent policy preserves legacy behavior. Unknown schemas and modes return
HTTP 400. An explicit `model_only` policy takes precedence over a contradictory
`tp_reading_context` or non-empty `allowed_knowledge_bases` value.

Successful responses echo the effective decision in the
`tp_knowledge_policy` object and `X-Hermes-Knowledge-Policy` header.
