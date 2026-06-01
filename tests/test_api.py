from __future__ import annotations

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import begin_stage_attempt, complete_stage_attempt, get_project, get_run, record_event
from novel_generator.services.openai_compatible import OpenAICompatibleClient
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
        "story_brief": {
            "setting": "A failing memory-city",
            "tone": "Tense luminous sci-fi",
            "protagonist": "Iris, disgraced archivist",
            "supporting_cast": ["Tarin, a guide", "Maelin, the city archivist"],
            "antagonist": "The city's coercive memory lattice",
            "core_conflict": "Save the city without accepting its control system",
            "ending_target": "One ending centered on consent and sacrifice",
            "world_rules": ["The city stores memory in living stone."],
            "must_include": ["A morally costly choice"],
            "avoid": ["Repeated inciting incidents"],
            "style_reference": "Short clipped sentences around tactile dread.",
            "style_targets": ["taut lyric pressure", "concrete sensory detail"],
            "dialogue_targets": ["subtext before confession"],
            "style_avoid": ["weight of everything"],
        },
    }


def create_project_and_run(
    client,
    *,
    pause_after_outline: bool = True,
    developmental_rewrite_enabled: bool | None = None,
    quality_profile: str = "balanced",
) -> tuple[str, str]:
    project_response = client.post("/api/projects", json=create_project_payload())
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    run_payload = {
        "project_id": project_id,
        "model_name": "test-model",
        "pause_after_outline": pause_after_outline,
        "quality_profile": quality_profile,
    }
    if developmental_rewrite_enabled is not None:
        run_payload["developmental_rewrite_enabled"] = developmental_rewrite_enabled

    run_response = client.post("/api/runs", json=run_payload)
    assert run_response.status_code == 201
    return project_id, run_response.json()["id"]


def test_project_and_run_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, run_id = create_project_and_run(client)

    detail_response = client.get(f"/api/runs/{run_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["project_id"] == project_id
    assert detail_response.json()["pause_after_outline"] is True
    assert detail_response.json()["developmental_rewrite_enabled"] is True
    assert detail_response.json()["quality_profile"] == "balanced"

    cancel_response = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "canceled"


def test_run_stage_attempts_api_returns_safe_attempt_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    _, run_id = create_project_and_run(client)
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        assert run is not None
        attempt = begin_stage_attempt(
            session,
            run,
            stage="story_bible",
            chapter_number=None,
            provider_name="ollama",
            model_name="test-model",
            metadata={"label": "story bible"},
        )
        complete_stage_attempt(session, attempt, '{"ok": true}')
        session.commit()

    response = client.get(f"/api/runs/{run_id}/attempts")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["stage"] == "story_bible"
    assert payload[0]["status"] == "success"
    assert payload[0]["output_chars"] == len('{"ok": true}')
    assert payload[0]["metadata"] == {"label": "story bible"}
    assert "messages" not in payload[0]


def test_project_patch_api_updates_story_brief_and_preserves_defaults(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_response = client.post("/api/projects", json=create_project_payload())
    project_id = project_response.json()["id"]
    with get_session_factory()() as session:
        project = get_project(session, project_id)
        assert project is not None
        story_brief = dict(project.story_brief or {})
        story_brief["approved_canon"] = [
            {
                "name": "Glass Orchard",
                "kind": "location",
                "role": "Project-level setting term",
                "aliases": ["the orchard"],
                "approved": True,
                "locked": True,
            }
        ]
        project.story_brief = story_brief
        session.commit()

    patch_response = client.patch(
        f"/api/projects/{project_id}",
        json={
            "title": "The Silver Orchard",
            "requested_chapters": 3,
            "story_brief": {
                "tone": "Claustrophobic mystery",
                "avoid": ["Looping emotional abstractions"],
                "style_targets": ["spare unease"],
                "dialogue_targets": ["questions that dodge the real wound"],
                "style_avoid": ["she felt"],
            },
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["title"] == "The Silver Orchard"
    assert patch_response.json()["requested_chapters"] == 3
    assert patch_response.json()["preferred_model"] == "test-model"
    assert patch_response.json()["story_brief"]["tone"] == "Claustrophobic mystery"
    assert patch_response.json()["story_brief"]["avoid"] == ["Looping emotional abstractions"]
    assert patch_response.json()["story_brief"]["style_reference"] == "Short clipped sentences around tactile dread."
    assert patch_response.json()["story_brief"]["style_targets"] == ["spare unease"]
    assert patch_response.json()["story_brief"]["dialogue_targets"] == ["questions that dodge the real wound"]
    assert patch_response.json()["story_brief"]["style_avoid"] == ["she felt"]
    assert patch_response.json()["story_brief"]["approved_canon"][0]["name"] == "Glass Orchard"
    assert patch_response.json()["story_brief"]["approved_canon"][0]["locked"] is True


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


def test_strict_quality_profile_enables_developmental_rewrite_by_default(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    _, run_id = create_project_and_run(client, quality_profile="strict")

    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["quality_profile"] == "strict"
    assert response.json()["developmental_rewrite_enabled"] is True


def test_invalid_quality_profile_is_rejected_when_queueing_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_response = client.post("/api/projects", json=create_project_payload())
    project_id = project_response.json()["id"]

    run_response = client.post(
        "/api/runs",
        json={
            "project_id": project_id,
            "model_name": "test-model",
            "quality_profile": "maximum",
        },
    )

    assert run_response.status_code == 422


def test_rerun_api_requeues_same_settings_as_v2_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, run_id = create_project_and_run(
        client,
        pause_after_outline=False,
        developmental_rewrite_enabled=True,
        quality_profile="strict",
    )
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
    assert rerun_response.json()["pipeline_version"] == 2
    assert rerun_response.json()["pause_after_outline"] is True
    assert rerun_response.json()["developmental_rewrite_enabled"] is True
    assert rerun_response.json()["quality_profile"] == "strict"
    assert rerun_response.json()["id"] != run_id


def test_resume_failed_run_api_requeues_same_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    _, run_id = create_project_and_run(client)
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        run.error_message = "Draft provider disconnected."
        session.commit()

    response = client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == run_id
    assert payload["status"] == "queued"
    assert payload["error_message"] is None
    assert payload["recovery_count"] == 1


def test_resume_run_api_rejects_non_failed_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    _, run_id = create_project_and_run(client)

    response = client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 409


def test_approve_outline_api_requeues_paused_run(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    _, run_id = create_project_and_run(client)
    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.AWAITING_APPROVAL
        run.current_step = "outline_review"
        session.commit()

    approve_response = client.post(f"/api/runs/{run_id}/approve-outline")

    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "queued"
    assert approve_response.json()["pause_after_outline"] is False


def test_delete_terminal_run_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, run_id = create_project_and_run(client)
    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.current_step = "completed"
        session.commit()

    delete_response = client.delete(f"/api/runs/{run_id}")

    assert delete_response.status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.get(f"/api/projects/{project_id}").status_code == 200


def test_delete_project_api_rejects_active_runs(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, _ = create_project_and_run(client)

    delete_response = client.delete(f"/api/projects/{project_id}")

    assert delete_response.status_code == 409
    assert "Cancel or finish active runs" in delete_response.json()["detail"]


def test_delete_project_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_response = client.post("/api/projects", json=create_project_payload())
    project_id = project_response.json()["id"]

    delete_response = client.delete(f"/api/projects/{project_id}")

    assert delete_response.status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_delete_finished_runs_for_project_api_flow(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])

    project_id, run_id = create_project_and_run(client)
    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        session.commit()

    delete_response = client.delete(f"/api/projects/{project_id}/runs/terminal")

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_runs"] == 1
    assert client.get(f"/api/runs/{run_id}").status_code == 404

    with session_factory() as session:
        project = get_project(session, project_id)
        assert project is not None
        assert project.runs == []


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


def test_openai_compatible_provider_can_be_enabled_and_used_for_run_routing(client, monkeypatch) -> None:
    monkeypatch.setattr(OpenAICompatibleClient, "list_models", lambda self: ["editor-model", "qa-model"])

    config_response = client.post(
        "/api/providers/openai_compatible/config",
        json={
            "base_url": "http://openai-compatible.test/v1",
            "default_model": "editor-model",
            "api_key": "secret-token",
            "is_enabled": True,
        },
    )
    assert config_response.status_code == 200

    project_response = client.post(
        "/api/projects",
        json={
            **create_project_payload(),
            "preferred_provider_name": "openai_compatible",
            "preferred_model": "editor-model",
            "task_routing": {
                "manuscript_qa": {
                    "provider_name": "openai_compatible",
                    "model_name": "qa-model",
                }
            },
        },
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    run_response = client.post(
        "/api/runs",
        json={
            "project_id": project_id,
            "provider_name": "openai_compatible",
            "model_name": "editor-model",
            "task_routing": {
                "manuscript_qa": {
                    "provider_name": "openai_compatible",
                    "model_name": "qa-model",
                }
            },
        },
    )

    assert run_response.status_code == 201
    payload = run_response.json()
    assert payload["provider_name"] == "openai_compatible"
    assert payload["model_name"] == "editor-model"
    assert payload["task_routing"]["manuscript_qa"]["model_name"] == "qa-model"
