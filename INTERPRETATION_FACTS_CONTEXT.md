# Изолированная интерпретация проверенных фактов

`tp_execution_mode: interpretation_facts_v1` — opt-in для подготовленного
Cabinet пакета фактов ядра. Допустимы два непустых текстовых сообщения,
system и user; максимум 128 KiB UTF-8 суммарно. Запрещены stream,
`tp_reading_context`, инструменты и общие session headers.

Режим использует существующую границу strict context: пустая runtime history,
отдельный session ID, отсутствие общей session DB, памяти, файлов контекста,
tools и plugin hooks/middleware. Ответ содержит
`X-Hermes-Context-Isolation: strict-v1`. Cabinet обязан проверить этот заголовок.

Сам `interpretation_facts_v1` не меняет reasoning и `max_tokens`. Если Cabinet
дополнительно передаёт `tp_answer_format: core_statements_v1`, wrapper отключает
reasoning для короткого JSON со ссылками, сохраняя tier `max_tokens` без изменений.
Модель, провайдер, accounting и идемпотентность остаются в существующем контуре.
Астрологические правила и проверка происхождения пакета принадлежат Core/Cabinet;
wrapper обеспечивает только изоляцию исполнения.

База ветки: `96735aa9d0e37b0d11cbfdedf4303fe4293fc3c4`; 01.10.2026 этот commit
подтверждён в последних finished-deploy balanced/economy/strong production.
Это проверка deployment metadata, не утверждение о самостоятельно проверенном
runtime SHA. Изменения подготовлены локально; серверы не изменялись.

Проверки: unit `tests/test_hermes_sourced_text.py`; runtime
`tests/test_patched_hermes_accounting_integration.py` в образе из закреплённого
upstream digest. Runtime-тесты работают без сети и с подменёнными AI-вызовами.
