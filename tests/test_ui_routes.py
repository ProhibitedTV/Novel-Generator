from __future__ import annotations

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import create_project, create_run, get_project, get_run
from novel_generator.schemas import ProjectCreate, ProviderCapabilities, RunCreate
from novel_generator.services.ollama import OllamaClient


def reachable_status(default_model: str = "test-model", models: list[str] | None = None) -> ProviderCapabilities:
    return ProviderCapabilities(
        reachable=True,
        base_url="http://ollama.test",
        default_model=default_model,
        available_models=models if models is not None else ["test-model"],
    )


def unreachable_status(default_model: str = "test-model") -> ProviderCapabilities:
    return ProviderCapabilities(
        reachable=False,
        base_url="http://ollama.test",
        default_model=default_model,
        available_models=[],
        error="Connection refused",
    )


def seed_project(title: str = "Seed Project") -> str:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = create_project(
            session,
            ProjectCreate(
                title=title,
                premise="A seeded premise for route rendering.",
                desired_word_count=4000,
                requested_chapters=4,
                min_words_per_chapter=800,
                max_words_per_chapter=1200,
                preferred_model="test-model",
            ),
        )
        session.commit()
        return project.id


def seed_project_and_run() -> tuple[str, str]:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = create_project(
            session,
            ProjectCreate(
                title="Seed Project",
                premise="A seeded premise for route rendering.",
                desired_word_count=4000,
                requested_chapters=4,
                min_words_per_chapter=800,
                max_words_per_chapter=1200,
                preferred_model="test-model",
            ),
        )
        run = create_run(
            session,
            project,
            RunCreate(
                project_id=project.id,
                model_name="test-model",
                target_word_count=4000,
                requested_chapters=4,
                min_words_per_chapter=800,
                max_words_per_chapter=1200,
            ),
        )
        session.commit()
        return project.id, run.id


def test_home_empty_state_with_unreachable_ollama(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: unreachable_status(default_model))

    response = client.get("/")

    assert response.status_code == 200
    assert "Get ready in four steps" in response.text
    assert "Connect Ollama before you queue work." in response.text
    assert "Open provider settings" in response.text


def test_home_empty_state_with_reachable_ollama_but_no_models(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model, []))

    response = client.get("/")

    assert response.status_code == 200
    assert "Get ready in four steps" in response.text
    assert "Ollama is reachable, but no models are ready yet." in response.text


def test_home_connected_state_with_existing_projects_and_runs(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    seed_project_and_run()

    response = client.get("/")

    assert response.status_code == 200
    assert "Recent projects" in response.text
    assert "Recent runs" in response.text
    assert "Get ready in four steps" not in response.text


def test_project_new_page_renders_model_picker_hooks(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))

    response = client.get("/projects/new")

    assert response.status_code == 200
    assert 'data-model-input' in response.text
    assert 'data-model-choice' in response.text


def test_project_edit_validation_renders_inline_errors(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id = seed_project()

    response = client.post(
        f"/projects/{project_id}/edit",
        data={
            "title": "Seed Project",
            "premise": "A seeded premise for route rendering.",
            "desired_word_count": "4000",
            "requested_chapters": "4",
            "min_words_per_chapter": "900",
            "max_words_per_chapter": "800",
            "preferred_model": "test-model",
            "notes": "",
        },
    )

    assert response.status_code == 400
    assert "Max words per chapter must be greater than or equal to min words per chapter." in response.text
    assert 'value="800"' in response.text


def test_provider_settings_validation_and_live_actions_render(client, monkeypatch) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "health",
        lambda self, default_model: reachable_status(default_model, ["verified-model"]),
    )

    response = client.post(
        "/settings/providers/ollama",
        data={
            "base_url": "http://ollama.test",
            "default_model": "missing-model",
        },
    )

    assert response.status_code == 400
    assert "These changes were tested but not saved yet." in response.text
    assert 'data-provider-action="status"' in response.text
    assert 'data-provider-action="models"' in response.text


def test_run_detail_renders_stepper_and_event_log_hooks(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.RUNNING
        run.current_step = "chapter_draft"
        run.current_chapter = 2
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert 'data-run-detail' in response.text
    assert 'data-run-stepper' in response.text
    assert 'data-event-log' in response.text


def test_project_detail_renders_cleanup_controls_for_finished_runs(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.current_step = "completed"
        session.commit()

    response = client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    assert "Manage project" in response.text
    assert "Clean finished runs" in response.text
    assert f'action="/runs/{run_id}/delete"' in response.text


def test_cleanup_finished_runs_ui_removes_terminal_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        session.commit()

    response = client.post(f"/projects/{project_id}/runs/cleanup", follow_redirects=False)

    assert response.status_code == 303
    with session_factory() as session:
        assert get_run(session, run_id) is None


def test_project_delete_ui_blocks_active_runs(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, _ = seed_project_and_run()

    response = client.post(f"/projects/{project_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert "Cancel+or+finish+active+runs+before+deleting+this+project." in response.headers["location"]
    with get_session_factory()() as session:
        assert get_project(session, project_id) is not None


def test_project_delete_ui_removes_project_when_safe(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id = seed_project("Disposable Project")

    response = client.post(f"/projects/{project_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/projects")
    with get_session_factory()() as session:
        assert get_project(session, project_id) is None


def test_run_detail_renders_delete_for_terminal_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert f'action="/runs/{run_id}/delete"' in response.text
    assert "Delete run" in response.text


def test_run_delete_ui_removes_terminal_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.current_step = "completed"
        session.commit()

    response = client.post(f"/runs/{run_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/projects/{project_id}")
    with session_factory() as session:
        assert get_run(session, run_id) is None
