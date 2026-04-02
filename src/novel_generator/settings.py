from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Novel Generator"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite:///./novel_generator.db"
    artifacts_dir: Path = Path("artifacts")
    log_level: str = "INFO"
    ollama_base_url: str = "http://127.0.0.1:11434"
    default_model: str = "llama3.1:8b"
    max_concurrent_runs: int = Field(default=1, ge=1, le=4)
    worker_poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=30.0)
    ollama_timeout_seconds: float = Field(default=120.0, ge=10.0, le=1800.0)
    ollama_max_retries: int = Field(default=2, ge=0, le=5)
    chapter_summary_window: int = Field(default=4, ge=1, le=12)
    secret_key: str = "change-me-for-public-deployments"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return settings
