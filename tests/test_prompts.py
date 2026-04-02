from __future__ import annotations

from novel_generator.models import ChapterDraft, ChapterStatus, GenerationRun, Project, RunStatus
from novel_generator.services.prompts import build_outline_messages, parse_outline, rolling_context


def _project() -> Project:
    return Project(
        title="The Glass Orchard",
        premise="A disgraced archivist finds a living map under a failing city.",
        desired_word_count=40000,
        requested_chapters=3,
        min_words_per_chapter=1000,
        max_words_per_chapter=1800,
        preferred_model="test-model",
    )


def _run() -> GenerationRun:
    return GenerationRun(
        project_id="project-1",
        model_name="test-model",
        target_word_count=40000,
        requested_chapters=3,
        min_words_per_chapter=1000,
        max_words_per_chapter=1800,
        status=RunStatus.QUEUED,
        current_step="queued",
    )


def test_outline_prompt_mentions_constraints() -> None:
    project = _project()
    run = _run()

    messages = build_outline_messages(project, run)

    assert "Requested chapters: 3" in messages[1]["content"]
    assert "Title" in messages[1]["content"]


def test_parse_outline_fills_missing_entries() -> None:
    parsed = parse_outline("Chapter 1: Arrival | The map awakens.", requested_chapters=3)

    assert len(parsed) == 3
    assert parsed[0]["title"] == "Arrival"
    assert parsed[0]["summary"] == "The map awakens."
    assert parsed[2]["title"] == "Chapter 3"


def test_rolling_context_uses_recent_completed_chapters() -> None:
    first = ChapterDraft(
        chapter_number=1,
        title="Arrival",
        outline_summary="Wake the map.",
        summary="The archivist finds the map.",
        status=ChapterStatus.COMPLETED,
    )
    second = ChapterDraft(
        chapter_number=2,
        title="Descent",
        outline_summary="Enter the undercity.",
        summary="The descent reveals sentient tunnels.",
        status=ChapterStatus.COMPLETED,
    )
    third = ChapterDraft(
        chapter_number=3,
        title="Ashes",
        outline_summary="Face the caretaker.",
        summary="A caretaker offers a dangerous bargain.",
        status=ChapterStatus.COMPLETED,
    )

    context = rolling_context([first, second, third], window=2)

    assert "Chapter 2" in context
    assert "Chapter 3" in context
    assert "Chapter 1" not in context
