from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    llm_provider: str = "groq"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    anthropic_api_key: str | None = None
    tavily_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    database_dir: Path = Path("./data")
    url_validation_timeout: float = 8.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
