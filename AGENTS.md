# AGENTS.md — правила работы Codex в Hermes wrapper

Этот репозиторий требует особенно аккуратного разделения трёх вещей:

1. **upstream Hermes Agent** — внешний runtime, исходники которого не принадлежат этому репозиторию;
2. **наш wrapper/integration layer** — Docker/bootstrap, patches, accounting, runtime identity, MCP setup и release safeguards;
3. **фактически запущенный runtime** — конкретный image/upstream revision/wrapper commit/configuration, подтверждённый evidence.

Не смешивать эти уровни.

## Ветки и выпуск

- `main` — production line.
- `test` — постоянный test contour.
- Разработку вести в отдельной ветке от **актуального `test`**.
- PR разработки направлять в `test`.
- Promotion `test → main` и production deploy требуют отдельного release decision и post-deploy verification.
- Агент не должен автоматически merge/deploy production.

## Роль репозитория

`hermes-agent-coolify` — deploy/runtime wrapper и integration layer вокруг pinned/upstream Hermes Agent.

Этот repo может содержать собственную важную логику вокруг:

- image/bootstrap/patching;
- fixed-model runtime identity;
- MCP registration/policy;
- accounting evidence и durable reconciliation;
- health/release identity;
- fail-closed startup/runtime checks.

Но наличие wrapper-кода **не означает**, что здесь находится весь исходный код Hermes gateway/agent loop.

Центральная архитектурная память: `dmirtsev/tp-architecture`.
Для AI Platform/Hermes задач использовать соответствующие ADR/spec и `AI_AGENTS.md` как реестр продуктовых AI-компонентов, а не как инструкцию coding agent.

## Критическая ловушка: stale status docs

`README_STATUS.md` и deploy README могут быть полезны как исторический/операционный контекст, но их даты и примеры конфигурации могут отставать от текущих tier runtimes и contracts.

Перед утверждением о текущей модели, endpoint, MCP wiring, upstream version или runtime SHA проверять:

1. current code/manifest в ветке;
2. pinned upstream image/revision/digest;
3. runtime identity/health evidence;
4. актуальный central architecture/release evidence.

Не использовать старое `latest`, старую model default или старый domain example как доказательство текущего runtime.

## Минимальный контекст по типу задачи

### Wrapper / Docker / boot

Читать Dockerfile, launcher/setup/patch code и relevant tests. Upstream source подключать только если patch зависит от конкретной upstream implementation detail.

### Model routing / tiers

Проверять fixed-model contract, server-side tier identity, current setup tests и central AI Platform decision. Не предполагать, что OpenAI-compatible request `model` реально маршрутизирует upstream model, если pinned runtime этого не гарантирует.

### Accounting / billing evidence

Проверять durable accounting state machine, idempotency, provider evidence, retry/restart cases и schema contract. Не оценивать monetary cost по догадке, если архитектура требует authoritative provider evidence.

### MCP / product knowledge / GBrain

Проверять registration + authorization + product policy + consumer contract. MCP tool availability не означает permission на все данные/действия.

### Runtime / deployment claim

Нужны exact wrapper commit, upstream identity, environment/tier identity, health/release evidence и при необходимости authenticated smoke.

## Security

- Никогда не читать/печатать/коммитить `.env`, API keys, bearer tokens, Authorization headers или runtime secrets.
- Не переносить secret values из Coolify/runtime в документацию или test fixtures.
- Service credentials и routing policy должны оставаться server-side.
- Fail-closed startup/security checks нельзя ослаблять ради удобства локального запуска без отдельной постановки.
- Не выдавать MCP discovery за authorization.

## Upstream patching

Если wrapper патчит upstream Hermes:

1. определить точную pinned upstream revision/digest;
2. проверить, что target symbol/behavior действительно существует в этой версии;
3. покрыть patch integration test или image smoke;
4. fail closed при несовместимой upstream версии, если silent degradation опасен;
5. не превращать временный patch в неявный fork без архитектурного решения.

## Accounting integrity

Особенно чувствительные invariants:

- один idempotency key не должен приводить к двойному provider dispatch/settlement;
- completed evidence должно переживать restart, если это обещано contract;
- unresolved/in-flight state не закрывать «по таймауту» без доказательства;
- provider generation/cost evidence не подменять estimate;
- concurrent worker/request context не должен смешиваться;
- Cabinet остаётся владельцем wallet/tariff policy, если ADR не меняет ownership.

## Архитектурно значимое изменение

Если меняются upstream pinning, model routing, MCP policy, accounting schema/state machine, runtime identity, deployment topology, auth boundary или external provider semantics:

- сверить central ADR/spec;
- обновить wrapper docs/contracts/tests;
- подготовить architecture handoff/evidence;
- не объявлять production capability до post-deploy verification.

## Перед работой Codex

1. Ветка от актуального `test`.
2. Одна конкретная цель и acceptance criteria.
3. Определить: задача про upstream, wrapper или deployed runtime?
4. Читать только нужный слой контекста.
5. Зафиксировать pinned upstream identity, если задача зависит от upstream behavior.
6. Запустить focused unit/image/integration tests по типу изменения.
7. PR в `test`.
8. Не выполнять merge/deploy автоматически.

## Если источники расходятся

Различай:

- **central accepted decision** — ADR/spec;
- **wrapper source fact** — код текущей ветки;
- **upstream fact** — конкретная pinned upstream версия;
- **runtime fact** — release/deployment evidence;
- **stale operations doc** — старый README/status snapshot;
- **нужно уточнить** — доказательств недостаточно.

Не делай вывод «Hermes умеет X» только из одного из этих слоёв без понимания, к какому уровню относится X.
