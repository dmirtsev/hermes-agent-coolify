# Hermes Runtime Decision

Дата: 2026-06-06

## Контекст

Сейчас `hermes-agent-coolify` является тонкой Docker-обвязкой над готовым образом:

```dockerfile
FROM nousresearch/hermes-agent:latest

CMD ["gateway", "run"]
```

Это было правильным быстрым стартом: Hermes поднят в Coolify, dashboard доступен по домену, GBrain подключён как MCP-сервер.

Но интеграция Кабинета с Hermes упёрлась в проблему: запросы к `POST https://hermes.astrogeoagent.ru/api/hermes/chat` доходят, но Hermes отвечает:

```json
{"error":"unauthenticated","detail":"Unauthorized","reason":"no_cookie","login_url":"/login"}
```

Это означает, что выбранный endpoint сейчас работает как браузерный/dashboard endpoint и требует cookie, а не как backend-to-backend API.

## Главное решение

Не заменять Hermes на GBrain, GP Brains или прямой LLM.

Правильная цель:

```text
Кабинет → настоящий Hermes Agent → GBrain/MCP/LLM/инструменты
```

GBrain остаётся памятью и инструментальным слоем. Hermes остаётся агентом.

## Что нужно выяснить

Перед созданием нового слоя или форка нужно понять, есть ли у образа `nousresearch/hermes-agent:latest` штатный программный вход:

1. Есть ли API endpoint без browser cookie?
2. Есть ли другой gateway/chat endpoint?
3. Можно ли включить bearer-token auth через env/config?
4. Есть ли CLI-команда для программного вызова агента?
5. Где в контейнере реализован `/api/hermes/chat`?
6. Можно ли использовать Hermes runtime как библиотеку/CLI из adapter?
7. Есть ли upstream repo или исходный код, который можно форкнуть?

## Варианты после диагностики

### Вариант A. Найден штатный API

Используем его напрямую из Кабинета:

```env
HERMES_AGENT_ENABLED=true
HERMES_AGENT_URL=https://hermes.astrogeoagent.ru/<official-api-endpoint>
HERMES_AGENT_TOKEN=<service-token>
```

В этом случае отдельный control layer на первом этапе не нужен.

### Вариант B. Найден CLI/internal runtime call

Делаем тонкий adapter, который вызывает Hermes через CLI/internal API:

```text
Кабинет → Adapter → Hermes CLI/internal runtime → Hermes Agent
```

Adapter не заменяет Hermes, а только открывает server-to-server вход.

### Вариант C. Найдены исходники Hermes Agent

Делаем fork и свой image:

```text
fork Hermes Agent
→ добавить POST /api/hermes/chat с Bearer auth
→ собрать ghcr.io/dmirtsev/hermes-agent:astrogeo
→ заменить Dockerfile wrapper на свой image
```

### Вариант D. API/CLI/исходников нет

Тогда официальный image остаётся лабораторией/dashboard, но не может быть центральным backend API для Кабинета. После этого принимается отдельное архитектурное решение.

## Текущий следующий шаг

Запустить диагностику внутри Coolify-контейнера Hermes.

В репозиторий добавлен скрипт:

```text
scripts/diagnose-hermes-runtime.sh
```

Его нужно запустить внутри контейнера Hermes и передать вывод обратно в архитектурный чат.

## Как запустить в Coolify

1. Открыть сервис Hermes в Coolify.
2. Открыть Terminal / Shell контейнера.
3. Выполнить:

```bash
sh /app/scripts/diagnose-hermes-runtime.sh
```

Если репозиторий не смонтирован в контейнер и файла нет, скопировать содержимое скрипта вручную или выполнить команды из скрипта по частям.

## Что особенно важно в выводе

Нужны блоки:

- `Hermes CLI help`
- `Listening ports`
- `Candidate files`
- `API route / auth grep`
- `Python package hints`

Не передавать секреты. Скрипт редактирует очевидные env secrets, но перед публикацией вывода всё равно проверить, что там нет токенов, ключей и паролей.

## Не делать пока

- Не создавать новый repo до диагностики.
- Не заменять Hermes на GP Brains/GBrain/LLM.
- Не пытаться использовать cookie dashboard auth в Кабинете.
- Не патчить контейнер вручную без фиксации в image/repo.
- Не завязываться на `latest` как на долгосрочную production-основу.

## После диагностики

После получения вывода выбрать один из путей:

1. прямой официальный API;
2. adapter к CLI/internal API;
3. fork Hermes Agent;
4. отдельное управляемое решение, если Hermes image оказывается закрытым и непригодным как backend API.
