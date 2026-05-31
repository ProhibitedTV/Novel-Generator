from __future__ import annotations

from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

from novel_generator.dependencies import get_session_factory, get_templates
from novel_generator.settings import get_settings


def _alembic_config() -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    return config


def test_quality_profile_migration_defaults_existing_runs(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    get_session_factory.cache_clear()
    get_templates.cache_clear()

    config = _alembic_config()
    command.upgrade(config, "0005_run_resilience")

    now = datetime.utcnow().isoformat()
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO projects (
                    id, title, premise, desired_word_count, requested_chapters,
                    min_words_per_chapter, max_words_per_chapter, preferred_model,
                    notes, created_at, updated_at, story_brief, preferred_provider_name,
                    task_routing
                )
                VALUES (
                    'project-1', 'Migration Project', 'Premise', 4000, 4,
                    800, 1200, 'test-model', NULL, :now, :now, '{}', 'ollama', '{}'
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO generation_runs (
                    id, project_id, source_run_id, model_name, target_word_count,
                    requested_chapters, min_words_per_chapter, max_words_per_chapter,
                    status, current_step, current_chapter, outline, summary_context,
                    error_message, cancel_requested, resume_from_chapter, created_at,
                    started_at, completed_at, updated_at, pipeline_version,
                    pause_after_outline, story_bible, continuity_ledger, provider_name,
                    task_routing, developmental_rewrite_enabled, worker_id,
                    last_heartbeat_at, recovery_count
                )
                VALUES (
                    'run-1', 'project-1', NULL, 'test-model', 4000,
                    4, 800, 1200, 'QUEUED', 'queued', NULL, NULL, NULL,
                    NULL, 0, NULL, :now, NULL, NULL, :now, 2,
                    1, NULL, NULL, 'ollama', '{}', 0, NULL, NULL, 0
                )
                """
            ),
            {"now": now},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        quality_profile = connection.scalar(sa.text("SELECT quality_profile FROM generation_runs WHERE id = 'run-1'"))
    assert quality_profile == "balanced"

    get_settings.cache_clear()
    get_session_factory.cache_clear()
    get_templates.cache_clear()
