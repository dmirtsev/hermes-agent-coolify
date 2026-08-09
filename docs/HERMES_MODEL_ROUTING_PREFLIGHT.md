# Hermes model routing preflight

Date: 2026-08-09

## Scope

This preflight inspected the exact digest pinned by this wrapper:

```text
nousresearch/hermes-agent@sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce
Hermes Agent v0.16.0 (2026.6.5)
upstream revision a38003be3d8ce87565915105b2d6261ba2cdb723
```

## Finding: request `model` is not an upstream model override

The pinned API server reads `body.model`, but only uses it in OpenAI-compatible
response metadata, request fingerprints, and session metadata. Agent creation
does not receive that value.

`APIServerAdapter._create_agent()` always resolves the inference model by
calling `gateway.run._resolve_gateway_model()`. That resolver reads
`model.default` (or `model.model`) from the persistent `config.yaml`.
`APIServerAdapter._run_agent()` has no model argument and every chat,
Responses API, and run path calls the same config-based `_create_agent()`.

Consequences:

- sending `{"model":"deepseek/..."}` does not select that OpenRouter model;
- the response may echo the caller's value even though a different configured
  upstream model executed the request;
- `HERMES_MODEL` is not the gateway's authoritative behavioral setting in this
  version; the gateway resolver explicitly treats `config.yaml` as the single
  source of truth;
- changing the persistent default while requests are active would be a global,
  race-prone configuration mutation and is not safe multi-user routing.

## Recommendation for sprint 1

Use three separately configured Hermes test runtimes for Economy, Balanced,
and Strong. Each runtime must have its own persistent storage and a fixed
`model.default`; Cabinet selects the endpoint from an administrator-published
allowlist. Do not accept arbitrary upstream model identifiers from browsers.

This is the safe option for the pinned Hermes version. A future single-runtime
optimization should be a separate change that adds an explicit per-request
route alias, server-side alias-to-model allowlist, actual-model reporting, and
concurrency/integration tests. It must not repurpose the current cosmetic
OpenAI `model` field without those controls.

The enforceable deployment contract is now documented in
`deploy/hermes-tiers/README.md`. When enabled, the wrapper atomically applies
the fixed model fields to the runtime's own persistent config and then derives
public health evidence by reading that YAML back. The API does not start if
the expected provider, model, or output-token cap differs from the persisted
configuration.

## Release evidence

The wrapper now records and publishes non-secret release identity:

- wrapper commit;
- deployment environment;
- wrapper build date when supplied;
- upstream Hermes version and revision;
- pinned upstream image digest;
- runtime start timestamp.

The evidence is written atomically to `${HERMES_HOME}/release.json`, logged at
startup, and included under `release` in `/health` and `/health/detailed`.

For Coolify, set these runtime-only values:

```env
HERMES_WRAPPER_COMMIT=$SOURCE_COMMIT
HERMES_DEPLOYMENT_ENVIRONMENT=test
HERMES_RELEASE_EVIDENCE_REQUIRED=true
```

Alternatively enable Coolify's **Include Source Commit in Build**, which passes
`SOURCE_COMMIT` as a Docker build argument. Runtime expansion is preferred when
preserving Docker build cache is more important.
