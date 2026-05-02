from __future__ import annotations

from novel_generator.dependencies import get_session_factory
from novel_generator.models import Artifact, RunStatus
from novel_generator.repositories import create_chapters_from_outline, create_project, create_run, get_project, get_run
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
                story_brief={
                    "setting": "A brittle station-city",
                    "tone": "tense sci-fi",
                    "protagonist": "A weary systems engineer",
                },
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
                story_brief={"setting": "A brittle station-city", "tone": "tense sci-fi"},
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


def test_project_new_page_renders_story_brief_and_model_picker_hooks(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))

    response = client.get("/projects/new")

    assert response.status_code == 200
    assert 'data-model-input' in response.text
    assert 'data-model-choice' in response.text
    assert 'name="story_setting"' in response.text
    assert "What happens after this" in response.text
    assert "Setup progress" in response.text


def test_notice_tone_renders_warning_notice_class(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))

    response = client.get("/?message=Heads+up&message_tone=warning")

    assert response.status_code == 200
    assert 'class="notice notice-warning"' in response.text


def test_project_edit_validation_preserves_story_brief_fields(client, monkeypatch) -> None:
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
            "story_setting": "Orbital city",
            "story_tone": "claustrophobic",
            "story_protagonist": "Nora",
            "story_supporting_cast": "Jun\nLiora",
            "story_antagonist": "Watcher lattice",
            "story_core_conflict": "Safety versus consent",
            "story_ending_target": "One ending only",
            "story_world_rules": "Memories are indexed in light",
            "story_must_include": "A betrayal",
            "story_avoid": "Looping chapter restarts",
        },
    )

    assert response.status_code == 400
    assert "Max words per chapter must be greater than or equal to min words per chapter." in response.text
    assert 'value="Orbital city"' in response.text
    assert "Looping chapter restarts" in response.text


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
    assert "Recommended setup flow" in response.text
    assert "OpenAI-compatible API" in response.text


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


def test_run_detail_renders_outline_approval_controls(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.AWAITING_APPROVAL
        run.current_step = "outline_review"
        run.story_bible = {
            "logline": "A trapped engineer finds a forbidden patch.",
            "theme": "Safety without consent is still captivity.",
            "act_plan": ["Setup", "Escalation", "Ending"],
            "cast": [],
            "character_agendas": [
                {
                    "name": "Nora",
                    "want": "Expose the patch",
                    "fear": "Becoming part of it",
                    "line_in_sand": "She will not ship a coercive cure.",
                    "stance_on_core_conflict": "Freedom over stability",
                    "relationship_to_protagonist": "Self",
                }
            ],
            "canon_registry": [
                {"name": "Peace Patch", "kind": "project", "role": "Behavioral control patch", "aliases": ["the patch"]}
            ],
            "conflict_ladder": ["Patch discovered", "Authority closes in", "Nora decides what ships"],
            "world_rules": ["The lattice edits memory."],
            "core_system_rules": ["Only signed patches can propagate."],
            "prose_guardrails": ["No abstract chapter endings."],
            "ending_promise": "One irreversible choice ends the crisis.",
        }
        run.outline = [
            {
                "chapter_number": 1,
                "act": "Act I",
                "title": "Signal",
                "objective": "Find the forbidden patch.",
                "conflict_turn": "The system locks Nora out.",
                "character_turn": "Nora stops hiding what she knows.",
                "reveal": "The patch carries her own signature.",
                "ending_state": "Nora commits to tracing the patch.",
                "outcome_type": "reversal",
                "primary_obstacle": "Authority lockdown",
                "cost_if_success": "Nora burns her admin access",
                "side_character_friction": "Jun wants to destroy the evidence",
                "concrete_ending_hook": {
                    "trigger": "A drone reaches the hatch",
                    "visible_object_or_actor": "Its lens turns blue",
                    "next_problem": "It speaks in Nora's own voice",
                },
            }
        ]
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Approve and continue" in response.text
    assert "Cancel and edit project" in response.text
    assert "Story bible" in response.text
    assert "Structured outline" in response.text
    assert "Character agendas" in response.text
    assert "Canon registry" in response.text
    assert "Concrete ending hook" in response.text


def test_run_detail_surfaces_qa_report_artifact(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.current_step = "completed"
        run.artifacts.append(
            Artifact(
                kind="qa-report",
                filename="qa-report.md",
                relative_path="run-id/qa-report.md",
                content_type="text/markdown",
            )
        )
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "editorial feedback" in response.text
    assert "qa-report.md" in response.text


def test_run_detail_surfaces_rich_chapter_qa_notes(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.outline = [
            {
                "chapter_number": 1,
                "act": "Act I",
                "title": "Signal",
                "objective": "Find the patch.",
                "conflict_turn": "The system isolates Nora.",
                "character_turn": "Nora stops hiding what she knows.",
                "reveal": "The patch is signed with her key.",
                "ending_state": "Nora commits to tracing it.",
            }
        ]
        create_chapters_from_outline(session, run)
        chapter = run.chapters[0]
        chapter.qa_notes = {
            "strengths": ["Forward motion holds."],
            "warnings": ["The ending still feels abstract."],
            "revision_required": True,
            "focus": ["Rewrite the final beat around the hatch alarm."],
            "forward_motion_score": 8,
            "ending_concreteness_score": 4,
            "cost_consequence_realism_score": 6,
            "side_character_independence_score": 5,
            "proper_noun_continuity_score": 9,
            "repetition_risk_score": 3,
            "blocking_issues": ["Chapter 1 ends in an abstract thesis statement instead of a concrete hook."],
            "soft_warnings": ["Chapter 1 may still need stronger side-character friction."],
            "repair_scope": "targeted_scene_and_ending",
        }
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Blocking issues" in response.text
    assert "Repair scope" in response.text
    assert "Ending concreteness: 4/10" in response.text


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
    assert "message_tone=warning" in response.headers["location"]
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
