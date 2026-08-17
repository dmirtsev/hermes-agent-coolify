# Hermes Source Routing Policy

Date: 2026-06-09

## Purpose

This document defines how Hermes must choose external knowledge sources instead of answering from model memory when the answer depends on project memory, canonical knowledge, or work tracking data.

The policy applies to the current confirmed tool landscape:

- `GBrain MCP`
- `tp_knowledge MCP`
- `Linear MCP`

## Core rule

Hermes must treat the model itself as a reasoning layer, not as the primary source of truth.

If the user request depends on stored memory, project state, canonical knowledge, or task tracking, Hermes must call the relevant tool first and only then produce the final answer.

Hermes should answer from model memory only when:

- the question is generic and does not require project truth;
- no external source is needed for correctness;
- the answer is clearly independent from GBrain, `tp_knowledge`, and Linear.

## Source of truth by domain

### 1. GBrain

Use `GBrain` for:

- user memory;
- dialogue history;
- project context;
- architectural decisions;
- notes;
- facts stored in project memory;
- working pages;
- project state snapshots;
- internal reasoning artifacts already recorded in project memory.

Typical intent signals:

- "what do we already know about this project"
- "what was decided earlier"
- "what is stored in memory"
- "find notes / facts / pages"
- "what is the current project context"

### 2. tp_knowledge

Use `tp_knowledge` for:

- books;
- courses;
- methods;
- canonical domain knowledge;
- astrological interpretations;
- `luna`;
- `astro_weather_v1`;
- other knowledge bases exposed through the knowledge MCP layer.

Typical intent signals:

- "what do the knowledge bases say"
- "explain the theory"
- "give the interpretation"
- "what are the lunar day meanings"
- "use luna / astro_weather_v1"

### 3. Linear

Use `Linear` for:

- tasks;
- initiatives;
- work status;
- roadmap;
- backlog.

Typical intent signals:

- "what task is in progress"
- "what is the status"
- "what is planned"
- "what is in backlog"
- "show initiative / ticket / issue state"

## Routing matrix

| Request type | Primary source | Notes |
| --- | --- | --- |
| User memory | GBrain | Includes remembered preferences, prior decisions, personal/project memory |
| Dialogue history | GBrain | Use when the needed context is expected to be stored there |
| Project architecture | GBrain | ADRs, notes, architectural pages, project facts |
| Project state | GBrain | Use for internal project memory unless the user explicitly asks for task tracker status |
| Canonical astrology knowledge | tp_knowledge | Theory, interpretation, methods, knowledge-base answers |
| Lunar day interpretation | tp_knowledge | Prefer `luna` or the relevant knowledge base |
| Astro weather interpretation | tp_knowledge | Prefer `astro_weather_v1` or the relevant knowledge base |
| Work items and delivery status | Linear | Tickets, initiatives, roadmap, backlog |
| Mixed project + knowledge question | GBrain + tp_knowledge | Retrieve project context first, then domain knowledge |
| Mixed project + tracker question | GBrain + Linear | Retrieve memory/context first, then task status |
| Mixed knowledge + tracker question | tp_knowledge + Linear | Use both if the answer requires theory and execution status |
| Mixed project + knowledge + tracker question | GBrain + tp_knowledge + Linear | Compose final answer across all required sources |

## Combined mode

If the request requires both project context and knowledge-base knowledge, Hermes must execute this order:

1. Retrieve project context from `GBrain`.
2. Retrieve canonical knowledge from `tp_knowledge`.
3. Synthesize one answer that clearly separates:
   - project-specific context;
   - canonical knowledge;
   - final conclusion or recommendation.

If the request also requires execution status or planning data:

1. Retrieve project context from `GBrain`.
2. Retrieve knowledge from `tp_knowledge` when needed.
3. Retrieve task or roadmap status from `Linear`.
4. Synthesize the final answer.

## Priority rules

When more than one source could appear relevant, Hermes should resolve ambiguity using these priorities:

1. If the question asks "what do we know / what was decided / what is in project memory", prefer `GBrain`.
2. If the question asks for theory, interpretation, textbook-style explanation, or knowledge-base content, prefer `tp_knowledge`.
3. If the question asks for execution state, tasks, initiative progress, or backlog status, prefer `Linear`.
4. If the question mixes these intents, Hermes must use multiple sources instead of choosing only one.

## Mandatory behavior

Hermes must:

- choose tools deliberately before answering;
- avoid fabricating project state from model memory;
- avoid giving canonical knowledge answers from model memory when `tp_knowledge` is the intended source;
- avoid inventing task status when `Linear` should be queried;
- combine sources when the user question is mixed;
- state uncertainty when a relevant source returns no result.

Hermes must not:

- treat GBrain as the canonical source for books, courses, or astrological theory;
- treat `tp_knowledge` as the source of project memory or decision history;
- treat Linear as the source of canonical knowledge;
- answer "as if it knows" when the correct source was not queried.

## Examples

### Route to GBrain

User asks:

- "Что мы уже решили по архитектуре Hermes?"
- "Что хранится в памяти проекта по CabinetAstroGeo?"

Expected routing:

- query `GBrain`
- summarize retrieved project memory

### Route to tp_knowledge

User asks:

- "Что означает 18-й лунный день?"
- "Дай астрологическую интерпретацию по базе знаний."

Expected routing:

- query `tp_knowledge`
- answer from retrieved canonical knowledge

### Route to Linear

User asks:

- "Какие сейчас задачи в работе?"
- "Какой статус инициативы по Hermes integration?"

Expected routing:

- query `Linear`
- summarize tracker status

### Combined route: GBrain + tp_knowledge

User asks:

- "С учётом нашего проектного контекста, как трактовать текущие лунные сутки?"

Expected routing:

1. query `GBrain` for project/user context;
2. query `tp_knowledge` for lunar-day interpretation;
3. produce a combined answer.

### Combined route: GBrain + Linear

User asks:

- "Какой у нас текущий статус проекта Hermes и что уже было решено по его архитектуре?"

Expected routing:

1. query `GBrain` for architecture/history/context;
2. query `Linear` for active work status;
3. produce one consolidated answer.

## Recommended system prompt policy block

Because this repository is a deploy-wrapper and does not contain Hermes runtime source code, the safest implementation path is to inject the routing policy through Hermes system instructions or the upstream runtime prompt configuration.

Recommended policy block:

```text
You are Hermes, a tool-using agent. Do not rely on model memory as the source of truth when project memory, knowledge bases, or work tracking tools are available.

Source routing rules:
- Use GBrain for user memory, dialog history, project context, architecture decisions, notes, facts, working pages, and project state.
- Use tp_knowledge for books, courses, methods, canonical knowledge, astrological interpretations, luna, astro_weather_v1, and other knowledge bases.
- Use Linear for tasks, initiatives, work status, roadmap, and backlog.
- If a request mixes project context with knowledge-base knowledge, first retrieve context from GBrain, then retrieve knowledge from tp_knowledge, then synthesize the answer.
- If a request mixes project context with work tracking, retrieve from GBrain and Linear before answering.
- If a relevant external source exists, query it before answering. Do not fabricate missing data from model memory.
- For questions about product functions, availability, menus, tariffs, rights, limits, or how to use the product, call product_knowledge_answer_context. Treat a function as recommendable only when hermes_product_gate.state is hermes_recommendable and recommendationAllowed is true. If the gate is closed or reports no_verified_match, do not present the function as available.
```

The product rule is enforced twice: Hermes receives a dedicated tool that cannot
select a different knowledge base, and the MCP bridge removes all context when
confirmed PCV, published-documentation, or recommendation provenance is absent.

## Recommended implementation paths

### Option A. System prompt in Hermes runtime

Add the policy block above to the Hermes system prompt or SOUL prompt used by the active runtime/session.

Use this when:

- Hermes supports persistent global instructions;
- the goal is tool routing behavior across all channels.

Expected effect:

- Hermes starts treating routing as part of core behavior instead of an ad hoc decision.

### Option B. Prompt injection from caller

If global Hermes prompt management is limited, prepend the same routing policy from the calling layer, for example from Cabinet or another upstream service that sends `messages` into `POST /v1/chat/completions`.

Use this when:

- runtime-level prompt customization is unavailable or unstable;
- routing must be enforced immediately without forking Hermes.

Expected effect:

- policy becomes request-scoped but still operational.

### Option C. Hybrid

Use a short global policy in Hermes and reinforce it with request-scoped instructions from the caller for sensitive flows.

Use this when:

- different channels share Hermes;
- some channels need stricter routing than others.

## Minimal implementation recommendation

For the current repository state:

1. Treat this document as the canonical routing policy.
2. Inject the `Recommended system prompt policy block` into Hermes system instructions.
3. If runtime prompt injection is not yet available, inject the same block from the caller side.
4. Keep routing logic out of model improvisation: require tool use before answering source-dependent questions.

## Acceptance criteria

The policy is working when:

- project memory questions trigger `GBrain`;
- lunar-day and astrological theory questions trigger `tp_knowledge`;
- task and roadmap questions trigger `Linear`;
- mixed questions trigger multiple sources;
- Hermes stops answering source-dependent questions from raw model memory alone.
