from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://ai_series:change_me_locally@localhost:5432/ai_series"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
