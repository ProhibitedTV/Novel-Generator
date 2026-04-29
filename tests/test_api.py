from __future__ import annotations

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import get_run, record_event
from novel_generator.services.ollama import OllamaClient


def create_project_payload() -> dict:
    return {
        "title": "The Glass Orchard",
        "premise": "A disgraced archivist finds a living map under a failing city.",
        "desired_word_count": 2000,
        "requested_chapters": 2,
        "min_words_per_chapter": 900,
        "max_words_per_chapter": 1200,
        "preferred_model": "test-model",
    }


def create_project_and_run(client) -> tuple[str, str]:
    project_response = client.post("/api/projects", json=create_project_payload())
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    run_response = client.post(
        "/api/runs",
        json={
            "project_id": project_id,
            "model_name": "test-model",
        },
    )
    assert run_response.status_code == 201
    return project_id, run_response.json()["id"]


def test_project_and_run_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, run_id = create_project_and_run(client)

    detail_response = client.get(f"/api/runs/{run_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["project_id"] == project_id

    cancel_response = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "canceled"


def test_project_patch_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_response = client.post("/api/projects", json=create_project_payload())
    project_id = project_response.json()["id"]

    patch_response = client.patch(
        f"/api/projects/{project_id}",
        json={
            "title": "The Silver Orchard",
            "requested_chapters": 3,
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["title"] == "The Silver Orchard"
    assert patch_response.json()["requested_chapters"] == 3
    assert patch_response.json()["preferred_model"] == "test-model"


def test_invalid_model_is_rejected_when_queueing_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_response = client.post("/api/projects", json=create_project_payload())
    project_id = project_response.json()["id"]

    run_response = client.post(
        "/api/runs",
        json={
            "project_id": project_id,
            "model_name": "missing-model",
        },
    )

    assert run_response.status_code == 400
    assert "not available" in run_response.json()["detail"]


def test_rerun_api_requeues_same_settings(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, run_id = create_project_and_run(client)
    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.current_step = "completed"
        session.commit()

    rerun_response = client.post(f"/api/runs/{run_id}/rerun")

    assert rerun_response.status_code == 201
    assert rerun_response.json()["project_id"] == project_id
    assert rerun_response.json()["model_name"] == "test-model"
    assert rerun_response.json()["status"] == "queued"
    assert rerun_response.json()["id"] != run_id


def test_run_events_sse_payload_shape_remains_compatible(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    _, run_id = create_project_and_run(client)
    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        record_event(session, run, "run_canceled", {"message": "Canceled in test."})
        session.commit()

    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        payload = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"event_type": "run_canceled"' in payload
    assert '"run_status": "canceled"' in payload
    assert "event: terminal" in payload
