# AI Series

Стартовый каркас проекта для разработки AI-сериала.

## Требования

- Docker Desktop (или Docker Engine + Compose)
- Git

## Первый запуск

1. Скопируйте `.env.example` в `.env`.
2. При желании измените локальный пароль PostgreSQL в `.env`.
3. Запустите:

   ```bash
   docker compose up --build
   ```

4. Откройте:
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000
   - Swagger: http://localhost:8000/docs
   - Health check: http://localhost:8000/health
   - Проверка БД: http://localhost:8000/health/db

Остановка: `docker compose down`.

Остановка с удалением локальных данных БД: `docker compose down -v`.

## Важно

`.env` содержит локальные настройки и не должен попадать в Git.
На этом этапе используем только локальные пароли-заглушки. Перед размещением в production
нужны секреты из Secret Manager, аутентификация, миграции и отдельные настройки окружения.
