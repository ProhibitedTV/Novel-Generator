from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import get_run, create_chapters_from_outline, create_run
from novel_generator.schemas import RunCreate
from novel_generator.services.ollama import OllamaClient
from novel_generator.routers.ui import _library_project_summary
from test_ui_routes import seed_project_and_run, outline_entry, reachable_status


def test_library_prioritizes_active_run_and_preserves_reading_shortcut(client, monkeypatch):
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, old_id = seed_project_and_run()
    with get_session_factory()() as session:
        old = get_run(session, old_id)
        old.outline = [outline_entry(n) for n in range(1, 5)]
        create_chapters_from_outline(session, old)
        session.flush()
        session.refresh(old, ["chapters"])
        old.chapters[0].content = "An existing saved draft."
        old.chapters[0].word_count = 4
        old.status = RunStatus.FAILED
        current = create_run(session, old.project, RunCreate(project_id=project_id, model_name="test-model"))
        current.status = RunStatus.AWAITING_APPROVAL
        session.commit()
        current_id = current.id
        summary = _library_project_summary(old.project)
        assert summary["focus"]["id"] == current_id
        assert summary["readable"]["id"] == old_id
        assert summary["readable"]["saved_chapters"] == 1
    for path in ("/", "/projects", f"/projects/{project_id}"):
        response = client.get(path)
        assert response.status_code == 200
        assert f'/runs/{current_id}#book-plan' in response.text
        assert f'/runs/{old_id}/read' in response.text
        assert "Read previous saved draft" in response.text
        assert "Saved prose does not establish editorial acceptance." in response.text


def test_failed_latest_draft_is_actionable(client, monkeypatch):
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, run_id = seed_project_and_run()
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        session.commit()
    response = client.get("/")
    assert "Pick up where you left off" in response.text
    assert "Review failure and resume" in response.text
    assert f'/runs/{run_id}/read' not in response.text
