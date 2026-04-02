from __future__ import annotations

from novel_generator.services.ollama import OllamaClient


def test_project_and_run_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_response = client.post(
        "/api/projects",
        json={
            "title": "The Glass Orchard",
            "premise": "A disgraced archivist finds a living map under a failing city.",
            "desired_word_count": 2000,
            "requested_chapters": 2,
            "min_words_per_chapter": 900,
            "max_words_per_chapter": 1200,
            "preferred_model": "test-model",
        },
    )
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
    run_id = run_response.json()["id"]
    assert run_response.json()["status"] == "queued"

    detail_response = client.get(f"/api/runs/{run_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["project_id"] == project_id

    cancel_response = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "canceled"
