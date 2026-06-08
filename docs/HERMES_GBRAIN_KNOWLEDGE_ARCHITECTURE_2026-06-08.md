# Hermes, GBrain и база знаний: архитектурное решение

Дата: 2026-06-08

## Контекст

В проекте «Точка притяжения / Кабинет / Hermes» сейчас есть три отдельных слоя:

```text
Hermes
GBrain
tp-knowledge / Knowledge Base
```

Они уже частично связаны между собой, но выполняют разные роли. В ходе проверки нужно было понять:

1. Что такое Hermes в текущей установке: полноценный агент или просто LLM endpoint.
2. Как Hermes взаимодействует с GBrain.
3. Как сейчас Кабинет взаимодействует с Hermes.
4. Как раньше происходило подключение к базе знаний.
5. Нужно ли подключать базу знаний к Hermes через MCP или достаточно старого REST-коннекта.
6. Где безопаснее развивать интеграцию: в Hermes, в GBrain, в Кабинете или через отдельный adapter.

## Текущее состояние Hermes

Hermes развёрнут через репозиторий-обёртку:

```text
dmirtsev/hermes-agent-coolify
```

Dockerfile обёртки запускает готовый образ Hermes Agent:

```dockerfile
FROM nousresearch/hermes-agent@sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce

CMD ["gateway", "run"]
```

Ранее использовалось:

```dockerfile
FROM nousresearch/hermes-agent:latest
```

Это было рискованно, потому что `latest` может измениться и сломать рабочую связку. Поэтому образ был закреплён по digest.

Вывод: Hermes не является нашим исходным кодом. Мы используем готовый runtime `nousresearch/hermes-agent`, а репозиторий `hermes-agent-coolify` является deploy/wrapper-слоем для Coolify.

## Подтверждённый рабочий вход в Hermes

Кабинет и curl обращаются к Hermes через OpenAI-compatible endpoint:

```text
POST https://hermes.astrogeoagent.ru/v1/chat/completions
```

Минимальный формат запроса:

```json
{
  "model": "hermes-agent",
  "messages": [
    {
      "role": "user",
      "content": "Привет, Гермес. Кто ты?"
    }
  ]
}
```

Ответ приходит в OpenAI-compatible формате:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "..."
      }
    }
  ]
}
```

Для удобного чтения русскоязычного ответа в терминале используется:

```bash
jq -r '.choices[0].message.content'
```

## Как Кабинет сейчас взаимодействует с Hermes

Текущая цепочка в Кабинете:

```text
Frontend UI
↓
Wasp action sendHermesMessage
↓
backend operations.ts
↓
buildHermesEnvelope
↓
hermesAgentClient.ts
↓
POST /v1/chat/completions
↓
Hermes
```

Внутри Кабинета собирается расширенный envelope с полями:

```text
user.id
role
serviceCode
scenarioCode
accessContext
allowedKnowledgeBases
allowedTools
dailyWeatherContext
message
```

Но наружу в Hermes сейчас отправляются только:

```text
model
messages
```

То есть большая часть structured context пока превращается в текстовый prompt или остаётся внутри backend-логики Кабинета.

Вывод: текущая коммуникация Кабинета с Hermes является рабочей, но не fully structured-agent-based. Это OpenAI-compatible chat transport.

## Проверка: Hermes является агентом, а не просто LLM

Был выполнен тест через `/v1/chat/completions`:

```text
Гермес, есть ли у тебя доступ к GBrain? Если да, что ты можешь через него делать?
```

Hermes ответил, что GBrain подключён как MCP-сервер и перечислил возможности:

```text
поиск и чтение страниц
создание и обновление страниц
работа с тегами и связями
извлечение фактов
диагностика
кодовая аналитика
jobs
```

Далее был выполнен практический тест:

```text
Гермес, найди в GBrain страницы про Hermes.
```

Hermes нашёл две страницы:

```text
hermes-gbrain-smoke-test
hermes-astrogeo-agent-base-role
```

Затем был выполнен тест создания страницы:

```text
Гермес, создай в GBrain тестовую страницу hermes-cabinet-communication-test.
```

Hermes создал страницу:

```text
hermes-cabinet-communication-test
```

После фиксации Hermes image по digest был выполнен повторный smoke-test:

```text
Найди в GBrain страницу hermes-cabinet-communication-test и скажи, что в ней написано.
```

Hermes нашёл страницу и прочитал её содержимое.

Вывод: Hermes реально работает как агент с MCP, а не только как LLM endpoint.

Подтверждённая цепочка:

```text
Кабинет / curl
↓
Hermes /v1/chat/completions
↓
GBrain MCP
↓
поиск, чтение и создание страниц
```

## Роль GBrain

GBrain у нас находится в рабочем репозитории:

```text
dmirtsev/gbrain
```

Это полноценный рабочий репозиторий с правами на изменение. В отличие от Hermes, GBrain можно развивать напрямую.

Роль GBrain:

```text
память
рабочие заметки
проектные знания
факты
связи
страницы
синтез
производная проекция знаний
```

GBrain не является канонической базой знаний по астрологии. Это слой памяти, рабочих материалов, фактов, связей и проектного контекста.

## Роль tp-knowledge / Knowledge Base

База знаний `tp-knowledge` проектировалась нами отдельно.

В неё изначально был заложен REST API, а не MCP.

Основные endpoint'ы:

```text
POST /api/retrieval/hybrid-search/
POST /api/retrieval/answer-context/
```

Проверенный endpoint:

```text
POST https://knowledge.astrogeoagent.ru/api/retrieval/answer-context/
```

Проверенные базы знаний:

```text
luna
astro_weather_v1
```

`answer-context` возвращает:

```text
context_text
chunks
sources
material_id
chunk_id
knowledge_base_slug
project_slug
```

Роль `tp-knowledge`:

```text
канонический источник знаний
структурированная база материалов
поиск по утверждённым материалам
контекст для ответа Hermes
```

## Как раньше мы подключались к базе знаний

Раньше подключение к базе знаний происходило напрямую по REST:

```text
curl / backend / тестовый запрос
↓
tp-knowledge REST API
↓
/api/retrieval/answer-context/
↓
context_text + chunks + sources
```

Пример:

```bash
curl -X POST https://knowledge.astrogeoagent.ru/api/retrieval/answer-context/ \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Что означает Луна в Козероге?",
    "knowledge_base_slug": "luna",
    "top_k": 5,
    "limit": 5,
    "max_chars": 6000
  }'
```

Это работало без Hermes и без MCP.

## Вопрос: чем не устраивает прежний REST-коннект

Прежний REST-коннект нас устраивает для Кабинета.

Схема:

```text
Кабинет backend
↓
REST
↓
tp-knowledge
↓
context_text
↓
Hermes получает подготовленный prompt
```

Плюсы:

```text
просто
быстро
понятно
контроль остаётся в backend Кабинета
не нужно менять Hermes
```

Минусы:

```text
Hermes сам не знает, что у него есть база знаний как tool
Hermes не может самостоятельно решить, когда вызвать базу
интеграция живёт только в Кабинете
другие каналы Hermes не получают этот инструмент автоматически
```

## Что даёт MCP

MCP нужен не потому, что REST плохой. MCP нужен для Hermes.

Если подключить базу знаний как MCP, то Hermes увидит её как инструмент:

```text
Hermes
↓
MCP tool
↓
tp-knowledge
```

Плюсы MCP:

```text
Hermes сам выбирает, когда нужна база знаний
база знаний становится таким же tool, как GBrain
инструмент доступен во всех каналах Hermes: CLI, Telegram, Discord, web, Кабинет
не нужно каждый раз писать интеграцию в каждом клиенте
Hermes становится оркестратором знаний
```

## Решение

Принято решение:

```text
1. Не ломать прежний REST-коннект Кабинета с базой знаний.
2. Не встраивать tp-knowledge напрямую в исходники Hermes.
3. Не форкать Hermes ради одного tool.
4. Добавить для Hermes отдельный MCP-adapter к tp-knowledge.
```

Целевая архитектура:

```text
Пользователь / Кабинет
↓
Hermes
├─ GBrain MCP
│  └─ память, страницы, факты, проектный контекст
│
└─ tp-knowledge MCP
   └─ канонические знания, answer-context, источники
```

## Почему не core tool внутри Hermes

Hermes сообщил, что core tool добавляется через Python-код внутри репозитория Hermes:

```text
tools/your_tool.py
toolsets.py
registry.register()
```

Этот путь требует:

```text
форкать или менять Hermes
писать Python tool
пересобирать image
поддерживать свою версию Hermes
следить за совместимостью с upstream
```

Для нас это слишком рано и рискованно.

## Почему MCP-adapter лучше

MCP-adapter можно сделать отдельно:

```text
tp-knowledge-mcp
```

Он будет маленьким мостом:

```text
Hermes MCP call
↓
tp-knowledge-mcp
↓
REST API tp-knowledge
↓
answer-context
```

Так мы:

```text
не меняем Hermes
не ломаем GBrain
не переделываем базу знаний
сохраняем старый REST API
получаем инструмент Hermes
```

## Предлагаемый MVP tp-knowledge-mcp

Новый сервис:

```text
dmirtsev/tp-knowledge-mcp
```

Минимальный tool:

```text
knowledge_answer_context
```

Input:

```json
{
  "query": "string",
  "knowledge_base_slug": "luna",
  "top_k": 5,
  "limit": 5,
  "max_chars": 6000
}
```

Output:

```json
{
  "context_text": "...",
  "sources": [...],
  "chunks": [...]
}
```

Внутри adapter вызывает:

```text
POST https://knowledge.astrogeoagent.ru/api/retrieval/answer-context/
```

## Как Hermes должен увидеть новый tool

В Hermes config:

```yaml
mcp_servers:
  tp_knowledge:
    url: "https://knowledge.astrogeoagent.ru/mcp"
    timeout: 180
```

Если MCP-adapter будет отдельным сервисом:

```yaml
mcp_servers:
  tp_knowledge:
    url: "https://tp-knowledge-mcp.astrogeoagent.ru/mcp"
    timeout: 180
```

Hermes зарегистрирует tool с префиксом:

```text
mcp_tp_knowledge_knowledge_answer_context
```

Или аналогичным именем по правилам Hermes:

```text
mcp_{server_name}_{tool_name}
```

## Важное различение ролей

```text
Hermes
= пользовательский агент, интерфейс общения, оркестратор tools

GBrain
= память, проектный контекст, рабочие заметки, факты, связи

tp-knowledge
= канонический источник знаний, материалы, retrieval, answer-context

Cabinet
= пользовательский интерфейс, backend, авторизация, access context, сценарии
```

## Безопасность

В ходе диагностики в терминал были случайно выведены секреты из Docker env.

Правило на будущее:

```text
не публиковать полный docker inspect env
не отправлять в чат выводы с OPENAI_API_KEY, DATABASE_URL, SMTP_PASSWORD, TELEGRAM_BOT_TOKEN, JWT_SECRET
хранить секреты только в Coolify env
для диагностики использовать grep только по безопасным полям или редактировать вывод вручную
```

Рекомендация:

```text
перевыпустить скомпрометированные ключи и пароли
```

## Smoke-tests, которые нужно сохранить

Проверка Hermes:

```bash
curl -s -X POST "https://hermes.astrogeoagent.ru/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <HERMES_AGENT_TOKEN>" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Ответь одним словом: ping"}]}' \
  | jq -r '.choices[0].message.content'
```

Проверка Hermes -> GBrain:

```bash
curl -s -X POST "https://hermes.astrogeoagent.ru/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <HERMES_AGENT_TOKEN>" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Найди в GBrain страницу hermes-cabinet-communication-test и скажи, что в ней написано."}]}' \
  | jq -r '.choices[0].message.content'
```

Проверка tp-knowledge REST:

```bash
curl -s -X POST "https://knowledge.astrogeoagent.ru/api/retrieval/answer-context/" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Что означает Луна в Козероге?",
    "knowledge_base_slug": "luna",
    "top_k": 5,
    "limit": 5,
    "max_chars": 6000
  }' | jq
```

## Следующие шаги

1. Зафиксировать это решение в git.
2. Не трогать GBrain и Hermes без smoke-test.
3. Оставить REST-коннект Кабинета с tp-knowledge как рабочий путь.
4. Создать отдельный проект `tp-knowledge-mcp`.
5. Реализовать MCP tool `knowledge_answer_context`.
6. Подключить `tp-knowledge-mcp` в Hermes config.yaml.
7. Проверить, что Hermes видит новый tool через `hermes mcp list` / `hermes mcp test`.
8. Выполнить контрольные вопросы:

```text
Что означает Луна в Козероге?
Как Луна связана с эмоциями?
Что такое лунные сутки?
Какие особенности Луны в Овне?
```

## Итоговое решение коротко

```text
Старый REST-коннект Кабинета с базой знаний оставляем.

Для Hermes делаем отдельный tp-knowledge MCP-adapter.

Hermes становится оркестратором:
- GBrain для памяти и проектного контекста;
- tp-knowledge для канонических знаний.
```
