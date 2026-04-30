from __future__ import annotations

import json

from novel_generator.dependencies import get_session_factory
from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.repositories import create_project, create_run, get_run, recover_running_runs
from novel_generator.schemas import ProjectCreate, RunCreate
from novel_generator.services.pipeline import process_run_safe
from novel_generator.services.state import approve_outline_review
from novel_generator.settings import get_settings


def _story_bible_json() -> str:
    return json.dumps(
        {
            "logline": "A disgraced archivist finds a living map under a failing city.",
            "theme": "Control without consent becomes another form of ruin.",
            "act_plan": ["Discovery", "Descent", "Choice"],
            "cast": [
                {
                    "name": "Iris",
                    "role": "Archivist",
                    "desire": "Restore the city without surrendering herself to it",
                    "risk": "Becoming the tool of the system she hates",
                },
                {
                    "name": "Tarin",
                    "role": "Guide",
                    "desire": "Survive the undercity",
                    "risk": "Trusting the wrong version of Iris",
                },
            ],
            "world_rules": ["The city stores memory in living stone."],
            "core_system_rules": ["The map can rewrite routes and access memories embedded in walls."],
            "ending_promise": "Iris must choose between saving the city and keeping free will intact.",
        }
    )


def _outline_json(chapters: int) -> str:
    payload = {
        "chapters": [
            {
                "chapter_number": index,
                "act": "Act I" if index == 1 else ("Act II" if index < chapters else "Act III"),
                "title": f"Chapter {index} Title",
                "objective": f"Objective {index}",
                "conflict_turn": f"Conflict turn {index}",
                "character_turn": f"Character turn {index}",
                "reveal": f"Reveal {index}",
                "ending_state": f"Ending state {index}",
            }
            for index in range(1, chapters + 1)
        ]
    }
    return json.dumps(payload)


def _plan_json(index: int) -> str:
    return json.dumps(
        {
            "opening_state": f"Opening state {index}",
            "character_goal": f"Character goal {index}",
            "scene_beats": [f"Beat {index}.1", f"Beat {index}.2", f"Beat {index}.3", f"Beat {index}.4"],
            "conflict_turn": f"Conflict turn {index}",
            "ending_hook": f"Ending hook {index}",
        }
    )


def _critique_json(*, revision_required: bool) -> str:
    return json.dumps(
        {
            "strengths": ["The chapter advances the plot."],
            "warnings": ["The first draft needs sharper character distinction."] if revision_required else [],
            "revision_required": revision_required,
            "focus": ["Differentiate Iris from Tarin."] if revision_required else [],
        }
    )


def _continuity_json(index: int) -> str:
    return json.dumps(
        {
            "chapter_outcome": f"Outcome {index}",
            "current_patch_status": f"Patch status after chapter {index}",
            "character_states": {"Iris": f"State {index}", "Tarin": f"Support state {index}"},
            "world_state": f"World state {index}",
            "open_threads": [f"Open thread {index}"],
            "resolved_threads": [f"Resolved thread {index}"],
            "timeline_entry": f"Timeline entry {index}",
            "timeline": [f"Timeline entry {value}" for value in range(1, index + 1)],
        }
    )


def _qa_report_json() -> str:
    return json.dumps(
        {
            "overall_verdict": "Promising first draft with manageable repetition risk.",
            "strengths": ["The premise stays clear."],
            "warnings": ["The middle could use more concrete physical conflict."],
            "continuity_risks": ["Track how the map changes access permissions."],
            "repetition_risks": ["Watch for repeated control-versus-freedom phrasing."],
            "ending_coherence_notes": ["The ending promise is present and singular."],
            "lint_findings": [],
        }
    )


class FakeOllamaClient:
    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def chat(self, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        return next(self._responses)


def _create_project(session, *, requested_chapters: int = 2):
    return create_project(
        session,
        ProjectCreate(
            title="The Glass Orchard",
            premise="A disgraced archivist finds a living map under a failing city.",
            desired_word_count=2000,
            requested_chapters=requested_chapters,
            min_words_per_chapter=900,
            max_words_per_chapter=1200,
            preferred_model="test-model",
            story_brief={
                "setting": "A failing memory-city",
                "tone": "Tense luminous sci-fi",
                "protagonist": "Iris, disgraced archivist",
                "core_conflict": "Save the city without accepting coercive control",
                "ending_target": "One clear ending centered on consent and sacrifice",
            },
        ),
    )


def test_process_run_safe_pauses_after_outline_when_requested(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(session, run, settings, FakeOllamaClient([_story_bible_json(), _outline_json(2)]))

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.AWAITING_APPROVAL
        assert refreshed.current_step == "outline_review"
        assert refreshed.story_bible is not None
        assert refreshed.outline is not None
        assert len(refreshed.chapters) == 2
        assert len(refreshed.artifacts) == 0


def test_approved_run_completes_and_generates_qa_report(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(session, run, settings, FakeOllamaClient([_story_bible_json(), _outline_json(2)]))

        paused = get_run(session, run.id)
        assert paused is not None
        approve_outline_review(session, paused)
        session.commit()

        resume_client = FakeOllamaClient(
            [
                _plan_json(1),
                "The first chapter prose lands the inciting incident cleanly.",
                _critique_json(revision_required=False),
                "Iris discovers the living map and commits to following it underground.",
                _continuity_json(1),
                _plan_json(2),
                "The second chapter escalates the danger in the undercity.",
                _critique_json(revision_required=False),
                "Iris and Tarin descend farther and realize the city has been steering them.",
                _continuity_json(2),
                _qa_report_json(),
            ]
        )
        process_run_safe(session, paused, settings, resume_client)

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.COMPLETED
        assert len(refreshed.chapters) == 2
        assert all(chapter.status == ChapterStatus.COMPLETED for chapter in refreshed.chapters)
        assert all(chapter.content for chapter in refreshed.chapters)
        assert all(chapter.summary for chapter in refreshed.chapters)
        assert all(chapter.continuity_update for chapter in refreshed.chapters)
        assert len(refreshed.artifacts) == 3
        assert any(artifact.kind == "qa-report" for artifact in refreshed.artifacts)
        assert refreshed.summary_context is not None


def test_revision_pass_updates_chapter_and_continuity_ledger(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    "Chapter 1\n\nThe first draft repeats itself too much.",
                    _critique_json(revision_required=True),
                    "Chapter 1: Revision\n\nThe revised draft sharpens Iris and Tarin into distinct voices.",
                    "Iris chooses the undercity mission and Tarin finally believes her.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        chapter = refreshed.chapters[0]
        assert refreshed.status == RunStatus.COMPLETED
        assert chapter.qa_notes is not None
        assert chapter.qa_notes["revision_required"] is True
        assert chapter.content.startswith("The revised draft")
        assert "Chapter 1" not in chapter.content
        assert refreshed.continuity_ledger["current_patch_status"] == "Patch status after chapter 1"


def test_v1_regeneration_creates_fresh_v2_structure(configured_environment) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        source_run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        source_run.pipeline_version = 1
        source_run.outline = [{"chapter_number": 1, "title": "Legacy", "summary": "Old outline"}]
        source_run.status = RunStatus.COMPLETED
        session.flush()

        regenerated = create_run(
            session,
            project,
            RunCreate(
                project_id=project.id,
                model_name="test-model",
                source_run_id=source_run.id,
                resume_from_chapter=2,
            ),
        )
        session.commit()

        assert regenerated.pipeline_version == 2
        assert regenerated.source_run_id is None
        assert regenerated.resume_from_chapter is None
        assert regenerated.outline is None
        assert regenerated.story_bible is None


def test_recover_running_runs_marks_runs_as_queued(configured_environment) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        run.status = RunStatus.RUNNING
        session.commit()

        recovered = recover_running_runs(session)
        session.commit()

        refreshed = get_run(session, run.id)
        assert recovered == 1
        assert refreshed.status.value == "queued"
