from __future__ import annotations

from datetime import datetime
import json

from novel_generator.dependencies import get_session_factory
from novel_generator.models import Artifact, ChapterStatus, RunStatus
from novel_generator.repositories import (
    begin_stage_attempt,
    create_chapters_from_outline,
    create_project,
    create_run,
    fail_stage_attempt,
    get_project,
    get_run,
    record_event,
)
from novel_generator.schemas import ProjectCreate, ProviderCapabilities, RunCreate
from novel_generator.services.ollama import OllamaClient
from novel_generator.settings import get_settings


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


def outline_entry(chapter_number: int, total_chapters: int = 4) -> dict[str, object]:
    if chapter_number <= max(1, total_chapters // 4):
        act = "Act I"
    elif chapter_number <= max(2, (total_chapters * 3) // 4):
        act = "Act II"
    else:
        act = "Act III"
    mode_cycle = ["setup", "setback", "reversal", "aftermath"]
    mode = mode_cycle[(chapter_number - 1) % len(mode_cycle)]
    return {
        "chapter_number": chapter_number,
        "act": act,
        "title": f"Signal {chapter_number}",
        "objective": f"Advance route {chapter_number}.",
        "conflict_turn": f"The pressure changes at marker {chapter_number}.",
        "character_turn": f"Nora makes a sharper choice in chapter {chapter_number}.",
        "reveal": f"The station reveals clue {chapter_number}.",
        "ending_state": f"The next route opens after chapter {chapter_number}.",
        "outcome_type": "reversal" if chapter_number % 3 == 0 else "setback",
        "primary_obstacle": f"Locked gate {chapter_number}",
        "cost_if_success": f"Nora spends leverage {chapter_number}.",
        "side_character_friction": "Jun wants a safer route.",
        "independent_side_character_move": "Jun moves the evidence cache.",
        "chapter_mode": mode,
        "civilian_life_detail": "Families trade heat cloths near the transit gate.",
        "emotional_reveal": "Nora admits stability has become another cage.",
        "ideology_pressure": "Authority argues safety requires consent to be optional.",
        "genre_specific_beats": ["visible clue", "tactical setback"],
        "genre_state_change": "The system shifts from watchful to hostile.",
        "concrete_ending_hook": {
            "trigger": f"Alarm {chapter_number} starts",
            "visible_object_or_actor": "A blue-lensed drone",
            "next_problem": f"The drone names route {chapter_number + 1}",
        },
    }


def seed_project_with_completed_compare_runs() -> tuple[str, list[str]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = create_project(
            session,
            ProjectCreate(
                title="Compare Project",
                premise="A seeded premise with multiple completed drafts.",
                desired_word_count=5000,
                requested_chapters=2,
                min_words_per_chapter=800,
                max_words_per_chapter=1200,
                preferred_model="test-model",
                story_brief={"setting": "A brittle station-city", "tone": "tense sci-fi"},
            ),
        )
        run_ids: list[str] = []
        for index, model_name in enumerate(["test-model", "alternate-model"], start=1):
            run = create_run(
                session,
                project,
                RunCreate(
                    project_id=project.id,
                    model_name=model_name,
                    target_word_count=5000,
                    requested_chapters=2,
                    min_words_per_chapter=800,
                    max_words_per_chapter=1200,
                    pause_after_outline=False,
                ),
            )
            run.status = RunStatus.COMPLETED
            run.current_step = "completed"
            run.completed_at = datetime(2026, 5, index, 12, 0)
            run.outline = [
                {"chapter_number": 1, "title": f"Signal {index}", "objective": "Find the signal.", "ending_state": "The signal is traced."},
                {"chapter_number": 2, "title": f"Choice {index}", "objective": "Choose a route.", "ending_state": "The route is chosen."},
            ]
            create_chapters_from_outline(session, run)
            for chapter in run.chapters:
                chapter.status = ChapterStatus.COMPLETED
                chapter.content = f"Completed draft {index} chapter {chapter.chapter_number} with enough prose to count."
                chapter.summary = f"Summary for draft {index} chapter {chapter.chapter_number}."
                chapter.word_count = 1100 + (index * 100)
                chapter.continuity_update = {"chapter_outcome": f"Outcome {chapter.chapter_number}", "current_patch_status": "Stable"}
                chapter.qa_notes = {
                    "strengths": [f"Draft {index} keeps the premise clear."],
                    "warnings": ["The ending needs more object-level specificity."] if index == 2 else [],
                    "revision_required": index == 2,
                    "focus": ["Sharpen the ending hook."] if index == 2 else [],
                    "forward_motion_score": 8 if index == 1 else 6,
                    "ending_concreteness_score": 8 if index == 1 else 4,
                    "cost_consequence_realism_score": 7 if index == 1 else 5,
                    "side_character_independence_score": 7 if index == 1 else 5,
                    "proper_noun_continuity_score": 8 if index == 1 else 6,
                    "repetition_risk_score": 2 if index == 1 else 7,
                    "emotional_depth_score": 8 if index == 1 else 5,
                    "ideology_clarity_score": 7 if index == 1 else 6,
                    "civilian_texture_score": 7 if index == 1 else 5,
                    "genre_contract_score": 8 if index == 1 else 5,
                    "technical_escalation_fatigue_score": 2 if index == 1 else 7,
                    "blocking_issues": ["Chapter ending is abstract."] if index == 2 else [],
                    "soft_warnings": ["Technical emergency beats repeat."] if index == 2 else [],
                    "genre_contract_findings": ["The draft needs a cleaner serial hook."] if index == 2 else [],
                    "repair_scope": "targeted_scene_and_ending" if index == 2 else "none",
                }
            run.artifacts.append(
                Artifact(
                    kind="qa-report",
                    filename=f"qa-report-{index}.md",
                    relative_path=f"{run.id}/qa-report.md",
                    content_type="text/markdown",
                )
            )
            run.artifacts.append(
                Artifact(
                    kind="manuscript-md",
                    filename=f"manuscript-{index}.md",
                    relative_path=f"{run.id}/manuscript.md",
                    content_type="text/markdown",
                )
            )
            run_ids.append(run.id)

        session.commit()
        return project.id, run_ids


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
    assert "Latest: No worker events yet." in response.text
    assert "Open run" in response.text
    assert "Get ready in four steps" not in response.text


def test_project_new_page_renders_story_brief_and_model_picker_hooks(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))

    response = client.get("/projects/new")

    assert response.status_code == 200
    assert 'data-model-input' in response.text
    assert 'data-model-choice' in response.text
    assert 'name="story_genre_profile"' in response.text
    assert 'name="story_setting"' in response.text
    assert 'name="story_style_targets"' in response.text
    assert 'name="story_dialogue_targets"' in response.text
    assert 'name="story_style_avoid"' in response.text
    assert 'name="story_style_reference"' in response.text
    assert "What happens after this" in response.text
    assert "Setup progress" in response.text
    assert "Runs locally on your configured Ollama host." in response.text
    assert "Ollama - Local/private" in response.text


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
            "story_genre_profile": "mystery",
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
            "story_style_targets": "taut lyric pressure\nconcrete sensory dread",
            "story_dialogue_targets": "subtext before confession",
            "story_style_avoid": "weight of everything",
            "story_style_reference": "Short clipped sentences around wet stone.",
        },
    )

    assert response.status_code == 400
    assert "Max words per chapter must be greater than or equal to min words per chapter." in response.text
    assert '<option value="mystery" selected>' in response.text
    assert 'value="Orbital city"' in response.text
    assert "Looping chapter restarts" in response.text
    assert "taut lyric pressure" in response.text
    assert "subtext before confession" in response.text
    assert "weight of everything" in response.text
    assert "Short clipped sentences around wet stone." in response.text


def test_project_edit_saves_prose_voice_fields(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id = seed_project()

    response = client.post(
        f"/projects/{project_id}/edit",
        data={
            "title": "Seed Project",
            "premise": "A seeded premise for route rendering.",
            "desired_word_count": "4000",
            "requested_chapters": "4",
            "min_words_per_chapter": "800",
            "max_words_per_chapter": "1200",
            "preferred_model": "test-model",
            "notes": "",
            "story_genre_profile": "sci_fi_thriller",
            "story_setting": "Orbital city",
            "story_tone": "claustrophobic",
            "story_protagonist": "Nora",
            "story_supporting_cast": "Jun",
            "story_antagonist": "Watcher lattice",
            "story_core_conflict": "Safety versus consent",
            "story_ending_target": "One ending only",
            "story_world_rules": "Memories are indexed in light",
            "story_must_include": "A betrayal",
            "story_avoid": "Looping chapter restarts",
            "story_style_targets": "taut lyric pressure\nconcrete sensory dread",
            "story_dialogue_targets": "subtext before confession",
            "story_style_avoid": "weight of everything",
            "story_style_reference": "Short clipped sentences around wet stone.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with get_session_factory()() as session:
        project = get_project(session, project_id)
        assert project is not None
        assert project.story_brief["style_targets"] == ["taut lyric pressure", "concrete sensory dread"]
        assert project.story_brief["dialogue_targets"] == ["subtext before confession"]
        assert project.story_brief["style_avoid"] == ["weight of everything"]
        assert project.story_brief["style_reference"] == "Short clipped sentences around wet stone."


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
    assert "Runs locally on your configured Ollama host." in response.text
    assert "This route may send manuscript text and story data to the configured external provider." in response.text
    assert "Full novel runs can use large prompts" in response.text


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
    assert 'data-run-stages-json' in response.text
    assert 'data-run-elapsed' in response.text
    assert "What The Worker Is Doing" in response.text
    assert "Run confidence" in response.text
    assert "Running: Draft chapter" in response.text
    assert "Next milestone" in response.text
    assert "Current Chapter Contract" in response.text
    assert "Quality Signals" in response.text
    assert "Continuity Highlights" in response.text


def test_run_detail_surfaces_fallback_recovery_guidance(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.RUNNING
        run.current_step = "outline"
        record_event(
            session,
            run,
            "outline_chunk_fallback",
            {"message": "Outline chunk 1-8 was unusable after repair; generated deterministic entries."},
        )
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Run confidence" in response.text
    assert "Running: Outline" in response.text
    assert "Outline chunk 1-8 was unusable after repair" in response.text
    assert "outline chunk fallback" in response.text


def test_failed_run_detail_surfaces_recovery_guidance(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.FAILED
        run.current_step = "outline"
        run.error_message = "Outline returned 29 chapters, but 64 were required."
        attempt = begin_stage_attempt(
            session,
            run,
            stage="outline",
            chapter_number=None,
            provider_name="ollama",
            model_name="test-model",
            metadata={"label": "structured outline"},
        )
        fail_stage_attempt(session, attempt, RuntimeError("Outline returned 29 chapters, but 64 were required."))
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Stopped during Outline" in response.text
    assert "Outline returned 29 chapters, but 64 were required." in response.text
    assert "Review outputs and recovery actions" in response.text
    assert "Recovery next step" in response.text
    assert "Resume from checkpoint" in response.text
    assert "Attempt diagnostics" in response.text
    assert "Run again" in response.text


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
    assert "Approval checklist" in response.text
    assert "Act distribution" in response.text
    assert "Chapter-mode distribution" in response.text
    assert 'data-outline-workspace' in response.text
    assert 'data-outline-act-filter' in response.text
    assert 'id="outline-chapter-1"' in response.text
    assert "Character agendas" in response.text
    assert "Canon registry" in response.text
    assert "Concrete ending hook" in response.text


def test_outline_review_workspace_surfaces_fallback_warnings(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.AWAITING_APPROVAL
        run.current_step = "outline_review"
        run.story_bible = {"logline": "A systems engineer finds a forbidden patch.", "theme": "Consent matters."}
        run.outline = [outline_entry(chapter_number) for chapter_number in range(1, 5)]
        record_event(
            session,
            run,
            "outline_chunk_started",
            {"message": "Generating outline chapters 1-4.", "start_chapter": 1, "end_chapter": 4},
        )
        record_event(
            session,
            run,
            "outline_chunk_fallback",
            {
                "message": "Outline chunk 1-4 was unusable after repair; generated deterministic entries.",
                "start_chapter": 1,
                "end_chapter": 4,
            },
        )
        record_event(
            session,
            run,
            "outline_chunk_completed",
            {"message": "Accepted outline chapters 1-4.", "start_chapter": 1, "end_chapter": 4, "chapters": 4},
        )
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Outline generation events" in response.text
    assert "Outline warnings" in response.text
    assert "Outline chunk 1-4 was unusable after repair" in response.text
    assert "Generated by fallback" in response.text
    assert 'data-outline-warning="true"' in response.text


def test_outline_review_workspace_renders_sixty_four_chapter_navigation(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.AWAITING_APPROVAL
        run.current_step = "outline_review"
        run.requested_chapters = 64
        run.story_bible = {"logline": "A systems engineer finds a forbidden patch.", "theme": "Consent matters."}
        run.outline = [outline_entry(chapter_number, total_chapters=64) for chapter_number in range(1, 65)]
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert response.text.count("data-outline-card") == 64
    assert 'id="outline-chapter-64"' in response.text
    assert 'data-outline-anchor="64"' in response.text
    assert 'data-outline-jump-input' in response.text
    assert "64 / 64" in response.text
    assert "Approve and continue" in response.text
    assert "Cancel and edit project" in response.text


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
    assert "Editorial next step" in response.text
    assert "Download QA report" in response.text
    assert "Publication export" in response.text


def test_completed_run_next_step_links_compare_when_available(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_ids = seed_project_with_completed_compare_runs()

    response = client.get(f"/runs/{run_ids[0]}")

    assert response.status_code == 200
    assert "Editorial next step" in response.text
    assert "Compare completed drafts" in response.text
    assert "2 completed drafts" in response.text


def test_completed_run_can_create_publication_export(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.current_step = "completed"
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
        chapter.status = ChapterStatus.COMPLETED
        chapter.title = "Signal"
        chapter.content = "Chapter 1\n\nNora opened the hatch."
        session.commit()

    detail_response = client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Export For Publication" in detail_response.text
    assert 'value="print_5x8"' in detail_response.text

    response = client.post(
        f"/runs/{run_id}/publication-export",
        data={"profile_id": "print_5x8", "include_ai_disclosure": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        publication_artifact = next(
            artifact for artifact in run.artifacts if artifact.kind == "publication-docx"
        )
        assert publication_artifact.filename == "publication-print-5x8.docx"
        assert publication_artifact.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert (get_settings().artifacts_dir / publication_artifact.relative_path).exists()
        assert any(event.event_type == "publication_export_created" for event in run.events)


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
                "side_character_friction": "Jun wants to destroy the evidence.",
                "independent_side_character_move": "Jun locks the hatch until Nora admits the risk.",
            }
        ]
        create_chapters_from_outline(session, run)
        chapter = run.chapters[0]
        run.status = RunStatus.RUNNING
        run.current_step = "chapter_revision"
        run.current_chapter = 1
        chapter.plan = json.dumps(
            {
                "attempt": "Nora spoofs the hatch sensor to buy ten seconds.",
                "complication": "Jun refuses to follow her if she burns the safety fuse.",
                "price_paid": "The spoof exposes their hiding place to the audit net.",
                "emotional_anchor": "Nora remembers the operator she failed to save.",
                "civilian_texture": "Children shelter in a maintenance chapel nearby.",
                "ideology_clash": "Jun wants safety even if it means obedience.",
                "independent_side_character_move": "Jun locks the hatch until Nora admits the risk.",
            }
        )
        chapter.summary = "Nora reaches the hatch, but the audit net tracks the spoof."
        chapter.continuity_update = {
            "chapter_outcome": "Nora escapes the hatch but exposes the team.",
            "current_patch_status": "Peace Patch replication paused at the hatch cluster.",
            "character_states": {"Nora": "committed", "Jun": "wavering"},
            "world_state": "Audit drones are narrowing the search grid.",
            "open_threads": ["Who signed the patch"],
            "resolved_threads": [],
            "timeline_entry": "The hatch spoof buys a brief escape window.",
            "timeline": ["The hatch spoof buys a brief escape window."],
            "new_entities_introduced": [],
            "entity_state_changes": {"Audit Net": "tracking Nora's spoof signature"},
            "open_promises_by_name": {"Jun": "Will he betray Nora to preserve station safety?"},
            "ideology_state_by_character": {"Jun": "Safety first", "Nora": "Consent first"},
            "ideology_shift_notes": {},
            "memory_damage": {},
            "trust_fractures": {"Nora/Jun": "Trust is eroding under pressure."},
            "civilian_pressure_points": ["Families are sheltering near the maintenance chapel."],
            "emotional_open_loops": {"Nora": "She cannot forget the last operator she failed to save."},
            "side_character_decisions": {"Jun": ["Jun locks the hatch until Nora admits the risk."]},
        }
        chapter.qa_notes = {
            "strengths": ["Forward motion holds."],
            "warnings": ["The ending still feels abstract."],
            "revision_required": True,
            "focus": ["Rewrite the final beat around the hatch alarm."],
            "ending_hook_type": "abstract_cliffhanger",
            "forward_motion_score": 8,
            "ending_concreteness_score": 4,
            "scene_turn_resolution_score": 4,
            "cost_consequence_realism_score": 6,
            "side_character_independence_score": 5,
            "proper_noun_continuity_score": 9,
            "repetition_risk_score": 3,
            "technical_escalation_fatigue_score": 7,
            "blocking_issues": ["Chapter 1 ends in an abstract thesis statement instead of a concrete hook."],
            "soft_warnings": ["Chapter 1 may still need stronger side-character friction."],
            "repair_scope": "targeted_scene_and_ending",
        }
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "QA risk" in response.text
    assert "Revision required" in response.text
    assert "Weak ending" in response.text
    assert "Technical fatigue" in response.text
    assert "Continuity" in response.text
    assert "Outcome logged" in response.text
    assert "Trust fracture" in response.text
    assert "data-review-section" in response.text
    assert "Blocking issues" in response.text
    assert "Repair scope" in response.text
    assert "Ending concreteness: 4/10" in response.text
    assert "Ending type: abstract_cliffhanger" in response.text
    assert "Scene turn resolved: 4/10" in response.text
    assert "Technical fatigue: 7/10" in response.text
    assert "Move: Jun locks the hatch until Nora admits the risk." in response.text
    assert "Repair scope used: targeted scene and ending." in response.text
    assert "Nora spoofs the hatch sensor to buy ten seconds." in response.text
    assert "Peace Patch replication paused at the hatch cluster." in response.text
    assert "Audit Net" in response.text
    assert "tracking Nora" in response.text


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


def test_project_detail_links_to_compare_when_multiple_completed_runs_exist(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, _ = seed_project_with_completed_compare_runs()

    response = client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    assert "Compare completed runs" in response.text
    assert f'href="/projects/{project_id}/runs/compare"' in response.text


def test_project_run_setup_warns_for_external_default_provider(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id = seed_project()

    session_factory = get_session_factory()
    with session_factory() as session:
        project = get_project(session, project_id)
        assert project is not None
        project.preferred_provider_name = "openai_compatible"
        project.preferred_model = "external-model"
        session.commit()

    response = client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    assert "OpenAI-compatible API - External provider" in response.text
    assert "This route may send manuscript text and story data to the configured external provider." in response.text
    assert "Full novel runs can use large prompts" in response.text
    assert "Chapter draft: OpenAI-compatible API" in response.text
    assert "Run developmental rewrite planning" in response.text
    assert "Developmental rewrite: OpenAI-compatible API" in response.text


def test_run_detail_warns_for_external_provider_routes(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    _, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.provider_name = "openai_compatible"
        run.model_name = "external-model"
        run.task_routing = {
            "chapter_critique": {"provider_name": "ollama", "model_name": "test-model"},
        }
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "External provider disclosure" in response.text
    assert "This route may send manuscript text and story data to the configured external provider." in response.text
    assert "Full novel runs can use large prompts" in response.text
    assert "Local/private" in response.text
    assert "Story bible: OpenAI-compatible API" in response.text
    assert "Chapter critique" in response.text


def test_project_canon_controls_add_update_lock_and_delete(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id = seed_project()

    response = client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    assert "Canon Registry" in response.text
    assert f'action="/projects/{project_id}/canon"' in response.text

    response = client.post(
        f"/projects/{project_id}/canon",
        data={
            "name": "Peace Patch",
            "kind": "project",
            "role": "Behavioral control patch",
            "aliases": "the patch, pacifier",
            "locked": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session_factory = get_session_factory()
    with session_factory() as session:
        project = get_project(session, project_id)
        assert project is not None
        canon = project.story_brief["approved_canon"]
        assert canon[0]["name"] == "Peace Patch"
        assert canon[0]["approved"] is True
        assert canon[0]["locked"] is True
        assert canon[0]["aliases"] == ["the patch", "pacifier"]

    response = client.post(
        f"/projects/{project_id}/canon/0/update",
        data={
            "name": "Peace Patch",
            "kind": "project",
            "role": "Locked deployment artifact",
            "aliases": "the patch\nThe Pacifier",
            "approved": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_factory() as session:
        project = get_project(session, project_id)
        assert project is not None
        canon = project.story_brief["approved_canon"]
        assert canon[0]["role"] == "Locked deployment artifact"
        assert canon[0]["aliases"] == ["the patch", "The Pacifier"]
        assert canon[0]["locked"] is False

    response = client.post(f"/projects/{project_id}/canon/0/lock", follow_redirects=False)

    assert response.status_code == 303
    with session_factory() as session:
        project = get_project(session, project_id)
        assert project is not None
        assert project.story_brief["approved_canon"][0]["locked"] is True

    response = client.post(f"/projects/{project_id}/canon/0/delete", follow_redirects=False)

    assert response.status_code == 303
    with session_factory() as session:
        project = get_project(session, project_id)
        assert project is not None
        assert project.story_brief["approved_canon"] == []


def test_run_story_bible_canon_can_be_approved_to_project(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, run_id = seed_project_and_run()

    session_factory = get_session_factory()
    with session_factory() as session:
        run = get_run(session, run_id)
        assert run is not None
        run.status = RunStatus.AWAITING_APPROVAL
        run.current_step = "outline_review"
        run.story_bible = {
            "logline": "A trapped engineer finds a forbidden patch.",
            "theme": "Consent beats stability.",
            "act_plan": ["Setup", "Escalation", "Ending"],
            "cast": [],
            "character_agendas": [],
            "canon_registry": [
                {"name": "Peace Patch", "kind": "project", "role": "Behavioral control patch", "aliases": ["the patch"]}
            ],
            "conflict_ladder": ["Patch discovered"],
            "world_rules": [],
            "core_system_rules": [],
            "prose_guardrails": [],
            "ending_promise": "Nora chooses consent.",
        }
        run.continuity_ledger = {
            "current_patch_status": "Patch unshipped.",
            "character_states": {},
            "world_state": "Station unstable.",
            "open_threads": [],
            "resolved_threads": [],
            "timeline": [],
            "active_entities": [
                {"name": "Peace Patch", "kind": "project", "role": "Behavioral control patch", "aliases": ["the patch"]}
            ],
            "entity_state_changes": {},
            "open_promises_by_name": {},
            "ideology_state_by_character": {},
            "memory_damage": {},
            "trust_fractures": {},
            "civilian_pressure_points": [],
            "emotional_open_loops": {},
            "genre_state": {},
        }
        session.commit()

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Pending approval" in response.text
    assert "Approve to project canon" in response.text
    assert "Story bible canon is pending approval: Peace Patch." in response.text

    response = client.post(f"/runs/{run_id}/canon/0/approve", follow_redirects=False)

    assert response.status_code == 303
    with session_factory() as session:
        run = get_run(session, run_id)
        project = get_project(session, project_id)
        assert run is not None
        assert project is not None
        assert run.story_bible["canon_registry"][0]["approved"] is True
        assert run.continuity_ledger["active_entities"][0]["approved"] is True
        assert project.story_brief["approved_canon"][0]["name"] == "Peace Patch"
        assert project.story_brief["approved_canon"][0]["approved"] is True


def test_run_compare_page_summarizes_completed_runs(client, monkeypatch) -> None:
    monkeypatch.setattr(OllamaClient, "health", lambda self, default_model: reachable_status(default_model))
    project_id, run_ids = seed_project_with_completed_compare_runs()

    response = client.get(f"/projects/{project_id}/runs/compare")

    assert response.status_code == 200
    assert "Run comparison" in response.text
    assert "Draft fitness" in response.text
    assert "test-model" in response.text
    assert "alternate-model" in response.text
    assert "Revision triggers" in response.text
    assert "Ending risks" in response.text
    assert "Technical fatigue" in response.text
    assert "qa-report-1.md" in response.text
    assert "manuscript-2.md" in response.text
    assert f'href="/runs/{run_ids[0]}"' in response.text


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
