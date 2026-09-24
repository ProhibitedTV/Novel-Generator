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
    openai_compatible_base_url: str = "http://127.0.0.1:1234/v1"
    openai_compatible_default_model: str = "local-model"
    openai_compatible_api_key: str = ""
    # App-side context-window knowledge for LM Studio/vLLM/etc. Zero means unknown. This value is
    # used only for prompt headroom/telemetry; it is not sent as an OpenAI API parameter.
    openai_compatible_context_tokens: int = Field(default=0, ge=0, le=1_048_576)
    openai_compatible_max_tokens: int = Field(default=8192, ge=256, le=65536)
    max_concurrent_runs: int = Field(default=1, ge=1, le=4)
    worker_poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=30.0)
    ollama_timeout_seconds: float = Field(default=120.0, ge=10.0, le=1800.0)
    ollama_max_retries: int = Field(default=2, ge=0, le=5)
    ollama_num_ctx: int = Field(default=32768, ge=2048, le=262144)
    ollama_num_predict: int = Field(default=8192, ge=256, le=65536)
    ollama_structured_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    provider_chat_timeout_seconds: float = Field(default=1800.0, ge=30.0, le=7200.0)
    provider_retry_backoff_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    provider_recovery_attempts: int = Field(default=96, ge=0, le=1000)
    run_stale_after_seconds: int = Field(default=3600, ge=60, le=86400)
    chapter_summary_window: int = Field(default=4, ge=1, le=12)
    autonomous_chapter_repair_attempts: int = Field(default=12, ge=1, le=100)
    autonomous_prose_repair_attempts: int = Field(default=12, ge=1, le=100)
    autonomous_targeted_repair_attempts: int = Field(default=12, ge=1, le=100)
    autonomous_manuscript_repair_rounds: int = Field(default=24, ge=1, le=100)
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
