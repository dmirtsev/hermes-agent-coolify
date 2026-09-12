# Hermes Curator on server runtimes

## Decision

Curator is **not part of a user answer** and is **not required for Hermes chat**.
On shared Coolify runtimes it is disabled by default until an administrator
explicitly approves a maintenance experiment.

If enabled for an experiment, Curator always uses the Economy model
`deepseek/deepseek-v4-flash`, including on Balanced and Strong runtimes. It
must never inherit the main runtime model.

## What Curator does

Curator is an upstream Hermes background maintainer for the local skill
library. After an idle interval it can:

- inspect agent-created skills and their usage metadata;
- mark unused skills stale;
- consolidate overlapping skills into broader skills;
- patch or create skill files;
- archive skills after taking a recoverable snapshot;
- write a run report under `logs/curator/`.

It is not an answer-quality reviewer, tariff router, user analytics service, or
required knowledge-retrieval component. User requests do not directly invoke
it. The gateway scheduler invokes it in the background.

## Why it is currently unnecessary on the server

The Coolify runtimes are immutable deployment units. Their canonical skills
should come from reviewed source and a new image, not from an autonomous process
rewriting a persistent runtime volume. The current server workflow has no
approved requirement for automatic skill consolidation. Curator therefore has
no demonstrated production value today.

## Cost and mutation risks

The pinned upstream implementation previously allowed `9999` agent iterations
and documents that a large review can take 50–100 API calls. Without an
auxiliary override, Curator inherits the runtime's main model. On a Strong
runtime that made it use Anthropic Opus even though no user selected Opus.

One iteration normally corresponds to one model round, but provider retries or
future upstream changes mean it must not be treated as the only financial
control. Key-level provider budgets and usage monitoring remain required before
automatic Curator runs are ever enabled.

## Enforced server controls

- automatic Curator is disabled by default;
- the Curator auxiliary slot is pinned to Economy;
- a single pass is capped at 12 agent iterations;
- values above 25 are rejected at runtime startup;
- built-in skills are never Curator candidates;
- the interval defaults to seven days;
- release evidence publishes enabled state, model, interval, cap, and policy
  validation without exposing credentials.

Environment contract:

- `HERMES_CURATOR_ENABLED=false`
- `HERMES_CURATOR_MODEL_DEFAULT=deepseek/deepseek-v4-flash`
- `HERMES_CURATOR_MAX_ITERATIONS=12`
- `HERMES_CURATOR_INTERVAL_HOURS=168`

## How an administrator may test it

Use an isolated test runtime and a separate budget-limited OpenRouter key. Set
`HERMES_CURATOR_ENABLED=true`, deploy the reviewed wrapper, run a dry-run first,
and compare the Curator report with provider usage. Do not enable it on all
tier runtimes merely to test the feature.

Production enablement requires a separate decision backed by a concrete skill
maintenance problem, an acceptable request budget, a rollback check, and a
named owner who reviews every report.
