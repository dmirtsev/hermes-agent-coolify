# HERMES_CHART_READING_MVP_TASK

Статус: implementation task draft
Проект: Точка Притяжения / Hermes Chart Intelligence
Репозиторий: dmirtsev/hermes-agent-coolify
Связанный источник знаний: dmirtsev/tp-knowledge

## 1. Цель задачи

Научить Hermes выполнять первый интеллектуальный разбор натальной карты по `chart_result_context_v2` с опорой на методику и правила из `tp-knowledge`.

Целевой вертикальный срез:

```text
chart_result_context_v2
↓
Hermes
↓
tp-knowledge method + rules
↓
первый структурный ответ по карте
↓
downstream chart_insight для GBrain
```

Важно: задача не про кнопку в Cabinet и не про запись в GBrain. На этом этапе Hermes должен подготовить ответ и объект insight, но фактическую запись в GBrain можно оставить следующей задачей.

## 2. Особенность этого репозитория

`hermes-agent-coolify` является deploy-wrapper вокруг Hermes Agent Docker image, а не полной копией исходного кода Hermes runtime.

Поэтому реализация может идти одним из путей:

1. через Hermes runtime configuration / dashboard / prompt policy;
2. через tool/client configuration в `/opt/data`;
3. через upstream caller, который формирует system/developer prompt и передаёт контекст в Hermes;
4. через отдельный bridge/service рядом с Hermes, если runtime нельзя расширить напрямую.

Постановка описывает требуемое поведение и контракты. Конкретное место реализации выбирается по реальным возможностям runtime, но публичное поведение должно совпасть с этой задачей.

## 3. Предусловия

`tp-knowledge` уже готов и на проде доступны:

```text
GET /api/knowledge-methods/
GET /api/knowledge-methods/{slug}/
POST /api/retrieval/rules/
```

Основная методика:

```text
natal_general_reading_method_v1
```

Hermes должен использовать `tp-knowledge` как source of truth для method + rules, а не держать методику только в prompt.

## 4. Входной контракт

Hermes должен принимать объект `chart_result_context_v2`.

Минимальная форма:

```json
{
  "context_type": "chart_result_context_v2",
  "result_version": "2.0",
  "status": "ready",
  "chart_identity": {},
  "calculation_context": {},
  "objects": [],
  "houses": [],
  "aspects": [],
  "warnings": [],
  "errors": [],
  "source": {
    "calculation_id": "string",
    "calculation_result_id": "string",
    "object_id": "string|null",
    "subject_id": "string|null"
  }
}
```

Дополнительно может приходить:

```json
{
  "access_context": {
    "user_id": "string",
    "role": "string",
    "tariff": "string",
    "allowed_tools": [],
    "allowed_knowledge_bases": [],
    "allowed_memory_scopes": []
  },
  "user_message": "Какие главные акценты ты видишь в моей карте?"
}
```

## 5. Определение режима

Если вход содержит:

```text
context_type = chart_result_context_v2
```

и пользователь спрашивает общий вопрос по карте, Hermes должен включить:

```text
Chart Reading Mode
```

Для MVP поддерживается только:

```text
domain: natal
task_type: general_reading
method_slug: natal_general_reading_method_v1
```

Если карта не натальная или task_type не general_reading, Hermes должен вернуть мягкий fallback:

```text
Сейчас я умею делать первый общий разбор натальной карты. Этот тип разбора пока не подключён.
```

## 6. Вызовы tp-knowledge

### 6.1 Получить методику

Hermes должен вызвать:

```text
GET {TP_KNOWLEDGE_BASE_URL}/api/knowledge-methods/natal_general_reading_method_v1/
```

Ожидает получить:

- method slug;
- domain;
- task_type;
- steps;
- rule_refs;
- rules;
- warnings.

### 6.2 Получить правила

Hermes должен вызвать:

```text
POST {TP_KNOWLEDGE_BASE_URL}/api/retrieval/rules/
```

Тело запроса:

```json
{
  "domain": "natal",
  "task_type": "general_reading",
  "query": "first chart reading general natal interpretation fact hypothesis",
  "limit": 30
}
```

Если method response уже содержит полный rules list, отдельный rules call всё равно допустим как контрольный или дополнительный источник, но не должен ломать ответ при недоступности.

## 7. Env/config

Добавить или задокументировать переменные:

```env
TP_KNOWLEDGE_BASE_URL=https://knowledge.astrogeoagent.ru
TP_KNOWLEDGE_API_KEY=optional_or_required_by_current_deploy
TP_KNOWLEDGE_TIMEOUT_SECONDS=10
```

Если API сейчас открыт без ключа внутри trusted network, `TP_KNOWLEDGE_API_KEY` можно оставить optional, но код/конфиг должен быть готов к заголовку:

```text
Authorization: Bearer <token>
```

## 8. Извлечение фактов карты

Hermes должен построить внутренний `chart_facts_summary` из `chart_result_context_v2`.

Минимальная форма:

```json
{
  "chart_type": "natal",
  "status": "ready|partial|error",
  "luminaries": {
    "sun": {},
    "moon": {}
  },
  "angles": {
    "asc": {},
    "mc": {}
  },
  "house_emphasis": [],
  "major_aspects": [],
  "warnings": [],
  "limitations": []
}
```

Правила:

- факты карты брать только из `chart_result_context_v2`;
- не выдумывать положения объектов;
- если Солнце, Луна, ASC, MC или аспекты отсутствуют, честно добавить limitation;
- если `status=partial`, ответ должен быть осторожным;
- если `errors` непустой и мешает разбору, не делать уверенный разбор.

## 9. Разделение фактов и интерпретаций

Hermes обязан внутренне и в ответе различать:

```text
факт карты
интерпретация
гипотеза
вопрос
ограничение
```

Пример:

```text
Факт: Солнце находится в 10 доме.
Гипотеза: тема реализации и видимого результата может быть значимой.
Вопрос: Насколько для вас сейчас важна тема профессионального проявления?
```

## 10. Формат ответа пользователю

Первый ответ должен быть коротким и структурным.

Рекомендуемая структура:

```text
Я вижу несколько главных акцентов.

1. Факты карты
2. 3-5 главных тем
3. Мягкие гипотезы
4. Что можно исследовать дальше
5. Вопрос пользователю
```

Ограничения:

- не писать полный трактат;
- не делать фаталистических выводов;
- не делать медицинских или юридических выводов;
- не придумывать данные карты;
- не ссылаться на знания, которые не были получены из `tp-knowledge`, как на source of truth;
- если knowledge API недоступен, явно сказать, что методический слой временно недоступен, и дать осторожный fallback.

## 11. Downstream insight для GBrain

Hermes должен подготовить объект `chart_insight_candidate`, но фактическая запись в GBrain может быть следующей задачей.

Форма:

```json
{
  "memory_type": "chart_insight",
  "user_id": "string|null",
  "calculation_result_id": "string|null",
  "object_id": "string|null",
  "topic": "general_natal_reading",
  "summary": "Пользователь начал общий разбор натальной карты. Hermes выделил основные темы и предложил направление продолжения.",
  "source": "hermes_chart_dialogue",
  "method_slug": "natal_general_reading_method_v1",
  "created_at": "datetime"
}
```

Если `access_context.user_id` или `source.calculation_result_id` отсутствует, соответствующие поля оставить null.

## 12. Fallback behavior

### tp-knowledge недоступен

Hermes должен:

1. не падать;
2. не утверждать, что использовал методику из базы;
3. сообщить мягко: методический слой временно недоступен;
4. дать короткий осторожный ответ только по фактам карты, если они есть;
5. не готовить strong insight для GBrain.

### chart_result_context_v2 неполный

Hermes должен:

1. перечислить ограничения;
2. не делать уверенных выводов;
3. предложить уточнить данные или перестроить карту.

### неизвестный chart_type

Вернуть:

```text
Сейчас я умею делать первый общий разбор натальной карты. Этот тип карты пока не подключён.
```

## 13. Smoke test MVP

Минимальный тестовый сценарий:

1. Подать Hermes тестовый `chart_result_context_v2` с натальной картой.
2. Спросить: `Какие главные акценты ты видишь в моей карте?`
3. Проверить, что Hermes вызвал method endpoint.
4. Проверить, что Hermes получил rules.
5. Проверить, что ответ содержит факты карты.
6. Проверить, что ответ содержит 3-5 тем.
7. Проверить, что ответ отделяет факты от гипотез.
8. Проверить, что есть следующий вопрос пользователю.
9. Проверить, что подготовлен `chart_insight_candidate`.

## 14. Acceptance Criteria

Задача считается выполненной, если:

1. Hermes принимает `chart_result_context_v2`.
2. Hermes распознаёт Chart Reading Mode для натальной карты.
3. Hermes вызывает `GET /api/knowledge-methods/natal_general_reading_method_v1/`.
4. Hermes вызывает или использует rules из `tp-knowledge`.
5. Hermes строит внутренний `chart_facts_summary`.
6. Hermes не выдумывает факты карты.
7. Hermes отвечает по структуре первого общего разбора.
8. Hermes мягко формулирует гипотезы.
9. Hermes задаёт следующий вопрос пользователю.
10. Hermes готовит `chart_insight_candidate` для будущей записи в GBrain.
11. При недоступности `tp-knowledge` Hermes не падает и даёт корректный fallback.
12. Реализация не требует изменений в Cabinet на этом этапе.

## 15. Что не делать в этой задаче

Не делать:

- кнопку в Cabinet;
- запись в GBrain;
- синастрию;
- транзиты;
- хорар;
- сравнение школ;
- загрузку книг;
- расширение `tp-knowledge`;
- полноценный UI для настроек Hermes;
- долгую персональную память.

## 16. Следующий шаг после выполнения

После успешного Hermes Chart Reading MVP следующая задача:

```text
GBrain Chart Insight MVP:
save prepared chart_insight_candidate into GBrain
```

После этого:

```text
Cabinet:
add “Обсудить карту с Гермесом” button on chart result screen
```
