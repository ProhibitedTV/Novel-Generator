from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_generator.bootstrap import run_migrations
from novel_generator.dependencies import get_session_factory, get_templates
from novel_generator.settings import get_settings


@pytest.fixture
def configured_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DEFAULT_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    get_session_factory.cache_clear()
    get_templates.cache_clear()
    run_migrations()
    yield tmp_path
    get_settings.cache_clear()
    get_session_factory.cache_clear()
    get_templates.cache_clear()


@pytest.fixture
def app(configured_environment: Path):
    from novel_generator.main import create_app

    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
