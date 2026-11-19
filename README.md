# AI Series

Стартовый каркас проекта для разработки AI-сериала.

## Требования

- Docker Desktop (или Docker Engine + Compose)
- Git

## Первый запуск

1. Скопируйте `.env.example` в `.env`.
2. Выберите провайдера через `AI_PROVIDER=groq` или `AI_PROVIDER=openai`,
   укажите соответствующий ключ (`GROQ_API_KEY` или `OPENAI_API_KEY`) и при
   необходимости задайте модель (`GROQ_MODEL` или `OPENAI_MODEL`).
3. При желании измените локальный пароль PostgreSQL в `.env`.
4. Запустите:

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

Чтобы сменить провайдера, измените `AI_PROVIDER` и соответствующий ключ в `.env`,
затем перезапустите backend: `docker compose up --build -d backend`.

Остановка с удалением локальных данных БД: `docker compose down -v`.

## Важно

`.env` содержит локальные настройки и не должен попадать в Git.
На этом этапе используем только локальные пароли-заглушки. Перед размещением в production
нужны секреты из Secret Manager, аутентификация, миграции и отдельные настройки окружения.
