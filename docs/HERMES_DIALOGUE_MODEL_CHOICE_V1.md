# Hermes dialogue model profiles — TOC-36

Status: implementation for review, not deployed. Production wrapper baseline:
`bb126df9100e98ee83e8bed38100f6921feec981`.
Paired Cabinet change: `docs/HERMES_DIALOGUE_MODEL_CHOICE_V1.md` in
`dmirtsev/CabinetAstroGeo`.

## Contract

Only authenticated Cabinet requests on the existing OpenRouter Economy runtime
may opt into an allowlisted profile via `tp_dialogue_model`. The matching model
ID, a durable Idempotency-Key, and non-streaming mode are required. The profile
is part of the exact request body hashed by the existing accounting journal.
Changing the profile under the same request key conflicts; replay returns the
original result without dispatching a second generation.

- `deepseek_flash`: DeepSeek Flash, client name «Искра»;
  model `deepseek/deepseek-v4-flash`, provider `novita/fp8`.
- `gpt_luna`: GPT Luna, client name «Луна»;
  model `openai/gpt-6-luna`, provider `openai`.

Both profiles use high reasoning, an 8192-token output limit, the same incoming
messages and system context, and the same existing factual/method validation.
Provider fallback and agent fallback are disabled for explicit selections.
Credentials and shared runtime configuration are unchanged. Every request gets
its own agent settings, so concurrent users cannot overwrite each other's
models. Accounting records the constructed agent model and provider-reported
generation evidence. Cosmetic response.model is not identity evidence.

Requests without the public code retain the previous fixed runtime behavior,
including its existing budgets and model. Balanced/Strong, onboarding and
planning clients do not acquire a new selection.

## Activation and rollback

Default: disabled. After approved code delivery to the Economy gateway, set
non-secret `HERMES_DIALOGUE_MODEL_CHOICE_ENABLED=true` there. It also requires
the existing `HERMES_RUNTIME_TIER=economy` and
`HERMES_FIXED_MODEL_PROVIDER=openrouter`; the actual provider URL is validated.
Health then exposes `dialogue_models` version 1 and the two codes. Only after
this readiness check should the paired Cabinet flag be enabled.

Keep the existing durable accounting volume and runtime identity. No new
secrets, funds transfers or production changes have been performed.
Before disabling the gateway flag, reconcile pending selected requests:
do not silently replay them using the old fixed model.
Production delivery requires separate explicit approval for the exact pair.

## Verification and remaining acceptance

The Docker patch fails when expected anchors in the digest-pinned upstream
source differ. It is applied after the existing accounting/context patches.
Local source verification used the reviewed pinned upstream revision
`a38003be3d8ce87565915105b2d6261ba2cdb723`.

Run `python3 -m unittest discover -s tests`. To exercise the real patched
constructor outside the built image, set `HERMES_PATCHED_API_SOURCE` to the
reviewed patched `gateway/platforms/api_server.py`. Tests cover concurrent
per-request construction, unchanged legacy settings, admission errors and
durable replay/model conflicts using synthetic data.

Local Docker is unavailable. Built-image boot/integration checks, live model
identity, actual billing and human dialogue acceptance remain required before
release. Real paid model calls have not been made in this implementation task.
