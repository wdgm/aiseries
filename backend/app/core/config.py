from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"

    database_url: str = (
        "postgresql+psycopg://ai_series:change_me_locally@localhost:5432/ai_series"
    )

    # --- AI provider ---------------------------------------------------
    # groq | openai | deepseek
    ai_provider: str = "groq"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    # enabled | disabled. JSON-извлечение по умолчанию без chain-of-thought:
    # дешевле и предсказуемее. Включайте, если качество извлечения важнее цены.
    deepseek_thinking: str = "disabled"

    ai_timeout_seconds: int = 120
    ai_max_output_tokens: int = 8192

    # --- Worker / queue ------------------------------------------------
    # Пусто = hostname контейнера (стабилен между рестартами одного контейнера).
    worker_id: str | None = None
    worker_poll_seconds: float = 2.0
    # Должен быть больше ai_timeout_seconds + время сохранения.
    worker_lease_seconds: int = 300
    task_max_attempts: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
