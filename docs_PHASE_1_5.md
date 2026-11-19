# Phase 1.5 — надёжная очередь анализа, учёт стоимости, целостность импорта

## Запуск
1. `.env`: `AI_PROVIDER=deepseek`, `DEEPSEEK_API_KEY=...` (см. `.env.example`).
2. `docker compose up -d --build` — backend применит миграцию `20261119phase15`, поднимется сервис `worker`.
3. Для книг, загруженных раньше: `docker compose exec backend python -m app.scripts.backfill_phase15 --dry-run`, затем без `--dry-run`.

## Как это работает
- Импорт ставит задачу в очередь (`analysis_jobs` + по строке `analysis_tasks` на фрагмент). Анализ выполняет `worker`, а не процесс API.
- Воркер берёт фрагменты через `FOR UPDATE SKIP LOCKED` с lease (`WORKER_LEASE_SECONDS`). Умер воркер — фрагмент вернётся в очередь (сразу при рестарте с тем же WORKER_ID, иначе по истечении lease).
- Сохранение результата и отметка «готово» — одна транзакция. Ответ LLM кэшируется в задаче: если упало сохранение, за LLM повторно не платим.
- Ошибки классифицируются: transient/rate_limit/invalid_output → повтор с backoff (до `TASK_MAX_ATTEMPTS`); auth/billing(402)/config → задача на паузе; bad_request/context_length → фрагмент `dead`.
- Каждый запрос к LLM (в т.ч. неудачный) → строка `ai_usage` с токенами, кэш-хитами и стоимостью по `provider_pricing` (у DeepSeek — пик/непик по UTC).

## API
- `GET  /api/v1/analysis-jobs/{id}` — прогресс, токены, `cost_usd`, `cost_complete`
- `POST /api/v1/analysis-jobs/{id}/resume` — доделать только незавершённое/упавшее
- `POST /api/v1/analysis-jobs/{id}/cancel`
- `GET  /api/v1/analysis-jobs/{id}/tasks?status=dead`
- `GET  /api/v1/books/{id}/analysis-jobs`
- `GET  /api/v1/ai-usage/summary?job_id=|book_id=`
- Повторная загрузка того же файла в проект → `409` с `existing_book_id`; провал проверки целостности → `422`.

## Тесты
`DATABASE_URL=... RUN_DB_TESTS=1 pytest backend/tests` (нужен Postgres с применёнными миграциями).

## Известные ограничения
- Запрос к LLM, который шёл в момент `kill -9`, провайдер мог списать, но в `ai_usage` его нет.
- Китайские праздники (весь день off-peak у DeepSeek) не учитываются — стоимость в эти дни завышена.
- `reanalyze` по-прежнему перезапускает все фрагменты (для доработки используйте `resume`).
- Дедупликация упоминаний в зоне перекрытия фрагментов (500 символов) — не в этой фазе.
