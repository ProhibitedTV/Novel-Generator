from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import ChapterStatus, GenerationRun, RunStatus
from ..repositories import create_chapters_from_outline, record_event, replace_artifacts
from ..schemas import (
    ChapterContinuityUpdate,
    ChapterCritique,
    ChapterPlan,
    ContinuityLedger,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
)
from ..settings import Settings
from .editorial import lint_manuscript, render_qa_report_markdown
from .exports import export_run_artifacts
from .ollama import OllamaClient, OllamaError, OllamaTransportError
from .prompts import (
    build_chapter_critique_messages,
    build_chapter_draft_messages,
    build_chapter_plan_messages,
    build_chapter_revision_messages,
    build_continuity_update_messages,
    build_json_repair_messages,
    build_manuscript_qa_messages,
    build_outline_messages,
    build_story_bible_messages,
    build_summary_messages,
    parse_chapter_critique,
    parse_chapter_plan,
    parse_continuity_update,
    parse_manuscript_qa_report,
    parse_outline,
    parse_story_bible,
    rolling_context,
    sanitize_chapter_content,
)


class RunCanceled(Exception):
    pass


def _sorted_chapters(run: GenerationRun) -> list:
    return sorted(run.chapters, key=lambda item: item.chapter_number)


def _require_requested_chapters(session: Session, run: GenerationRun) -> list:
    session.refresh(run, attribute_names=["chapters"])
    chapters = _sorted_chapters(run)
    if len(chapters) != run.requested_chapters:
        raise RuntimeError(
            f"Run expected {run.requested_chapters} chapter checkpoints, but only {len(chapters)} were available."
        )
    return chapters


def _sync_summary_context(run: GenerationRun, window: int) -> None:
    run.summary_context = rolling_context(run.chapters, window)


def _ensure_not_canceled(session: Session, run: GenerationRun) -> None:
    session.refresh(run)
    if run.cancel_requested:
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_canceled", {"message": "Cancellation was requested."})
        session.commit()
        raise RunCanceled("Run canceled.")


def _generate_structured_output(
    client: OllamaClient,
    model_name: str,
    build_messages: Callable[[], list[dict[str, str]]],
    parser: Callable[[str], Any],
    label: str,
) -> Any:
    raw_output = client.chat(model_name, build_messages())
    try:
        return parser(raw_output)
    except Exception:
        repaired_output = client.chat(model_name, build_json_repair_messages(raw_output, label))
        return parser(repaired_output)


def _build_initial_ledger(story_bible: StoryBible) -> ContinuityLedger:
    return ContinuityLedger(
        current_patch_status="No irreversible patch decision has been made yet.",
        character_states={
            member.name: f"Starting state: {member.desire} is threatened by {member.risk}."
            for member in story_bible.cast
        },
        world_state="The novel is still in its opening equilibrium.",
        open_threads=[story_bible.logline, story_bible.ending_promise],
        resolved_threads=[],
        timeline=["Opening state established."],
    )


def _story_bible_from_run(run: GenerationRun) -> StoryBible:
    if not run.story_bible:
        raise RuntimeError("Run is missing a story bible.")
    return StoryBible.model_validate(run.story_bible)


def _continuity_ledger_from_run(run: GenerationRun) -> ContinuityLedger:
    if not run.continuity_ledger:
        raise RuntimeError("Run is missing a continuity ledger.")
    return ContinuityLedger.model_validate(run.continuity_ledger)


def _outline_entry(run: GenerationRun, chapter_number: int) -> StructuredOutlineEntry:
    if not run.outline:
        raise RuntimeError("Run is missing a structured outline.")
    for item in run.outline:
        if int(item.get("chapter_number", 0)) == chapter_number:
            return StructuredOutlineEntry.model_validate(item)
    raise RuntimeError(f"Structured outline entry for chapter {chapter_number} was not found.")


def _ledger_from_update(update: ChapterContinuityUpdate) -> ContinuityLedger:
    timeline = update.timeline or [update.timeline_entry]
    if update.timeline_entry and update.timeline_entry not in timeline:
        timeline.append(update.timeline_entry)
    return ContinuityLedger(
        current_patch_status=update.current_patch_status,
        character_states=update.character_states,
        world_state=update.world_state,
        open_threads=update.open_threads,
        resolved_threads=update.resolved_threads,
        timeline=timeline,
    )


def _persist_structured_plan(chapter: Any, plan: ChapterPlan) -> None:
    chapter.plan = json.dumps(plan.model_dump(), indent=2)


def _persist_structured_qa(chapter: Any, critique: ChapterCritique) -> None:
    chapter.qa_notes = critique.model_dump()


def _generate_story_bible(session: Session, run: GenerationRun, settings: Settings, client: OllamaClient) -> StoryBible:
    project = run.project
    run.current_step = "story_bible"
    record_event(session, run, "story_bible_started", {"message": "Generating story bible."})
    session.commit()
    story_bible = _generate_structured_output(
        client,
        run.model_name,
        lambda: build_story_bible_messages(project, run),
        parse_story_bible,
        "story bible",
    )
    run.story_bible = story_bible.model_dump()
    run.continuity_ledger = _build_initial_ledger(story_bible).model_dump()
    record_event(session, run, "story_bible_completed", {"logline": story_bible.logline})
    session.commit()
    return story_bible


def _generate_outline(session: Session, run: GenerationRun, story_bible: StoryBible, client: OllamaClient) -> None:
    project = run.project
    run.current_step = "outline"
    record_event(session, run, "outline_started", {"message": "Generating structured outline."})
    session.commit()
    run.outline = _generate_structured_output(
        client,
        run.model_name,
        lambda: build_outline_messages(project, run, story_bible),
        lambda raw: parse_outline(raw, run.requested_chapters),
        "structured outline",
    )
    create_chapters_from_outline(session, run)
    record_event(session, run, "outline_completed", {"chapters": len(run.outline or [])})
    session.commit()


def _pause_for_outline_review(session: Session, run: GenerationRun) -> None:
    run.status = RunStatus.AWAITING_APPROVAL
    run.current_step = "outline_review"
    run.current_chapter = None
    record_event(session, run, "outline_ready_for_approval", {"message": "Outline is ready for approval."})
    session.commit()


def _draft_chapter(
    session: Session,
    run: GenerationRun,
    chapter: Any,
    outline_entry: StructuredOutlineEntry,
    story_bible: StoryBible,
    settings: Settings,
    client: OllamaClient,
) -> None:
    project = run.project
    ledger = _continuity_ledger_from_run(run)
    _sync_summary_context(run, settings.chapter_summary_window)
    run.current_chapter = chapter.chapter_number
    run.current_step = "chapter_plan"
    record_event(
        session,
        run,
        "chapter_planning",
        {"chapter_number": chapter.chapter_number, "title": chapter.title},
    )
    session.commit()

    plan = _generate_structured_output(
        client,
        run.model_name,
        lambda: build_chapter_plan_messages(
            project,
            run,
            chapter,
            outline_entry,
            story_bible,
            ledger,
            run.summary_context or "",
        ),
        parse_chapter_plan,
        f"chapter {chapter.chapter_number} plan",
    )
    _persist_structured_plan(chapter, plan)
    session.commit()

    _ensure_not_canceled(session, run)
    run.current_step = "chapter_draft"
    record_event(
        session,
        run,
        "chapter_drafting",
        {"chapter_number": chapter.chapter_number, "title": chapter.title},
    )
    session.commit()

    chapter.content = sanitize_chapter_content(
        client.chat(
            run.model_name,
            build_chapter_draft_messages(
                project,
                run,
                chapter,
                outline_entry,
                story_bible,
                ledger,
                run.summary_context or "",
                plan,
            ),
        )
    )
    if not chapter.content.strip():
        raise RuntimeError(f"Chapter {chapter.chapter_number} draft was empty.")
    chapter.word_count = len((chapter.content or "").split())
    session.commit()

    _ensure_not_canceled(session, run)
    run.current_step = "chapter_revision"
    critique = _generate_structured_output(
        client,
        run.model_name,
        lambda: build_chapter_critique_messages(project, chapter, outline_entry, story_bible, ledger),
        parse_chapter_critique,
        f"chapter {chapter.chapter_number} critique",
    )
    _persist_structured_qa(chapter, critique)
    if critique.revision_required:
        record_event(
            session,
            run,
            "chapter_revision_started",
            {"chapter_number": chapter.chapter_number, "title": chapter.title},
        )
        session.commit()
        chapter.content = sanitize_chapter_content(
            client.chat(
                run.model_name,
                build_chapter_revision_messages(
                    project,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    plan,
                    critique,
                ),
            )
        )
        if not chapter.content.strip():
            raise RuntimeError(f"Chapter {chapter.chapter_number} revision was empty.")
        chapter.word_count = len((chapter.content or "").split())
        session.commit()

    _ensure_not_canceled(session, run)
    run.current_step = "chapter_summary"
    chapter.summary = client.chat(run.model_name, build_summary_messages(chapter, outline_entry)).strip()
    if not chapter.summary:
        raise RuntimeError(f"Chapter {chapter.chapter_number} summary was empty.")
    continuity_update = _generate_structured_output(
        client,
        run.model_name,
        lambda: build_continuity_update_messages(project, chapter, ledger),
        parse_continuity_update,
        f"chapter {chapter.chapter_number} continuity update",
    )
    ledger_after = _ledger_from_update(continuity_update)
    if continuity_update.timeline != ledger_after.timeline:
        continuity_update.timeline = ledger_after.timeline
    chapter.continuity_update = continuity_update.model_dump()
    chapter.status = ChapterStatus.COMPLETED
    chapter.error_message = None
    run.continuity_ledger = ledger_after.model_dump()
    _sync_summary_context(run, settings.chapter_summary_window)
    record_event(
        session,
        run,
        "chapter_completed",
        {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "word_count": chapter.word_count,
        },
    )
    session.commit()


def _run_manuscript_qa(
    session: Session,
    run: GenerationRun,
    chapters: list,
    client: OllamaClient,
) -> tuple[ManuscriptQaReport, str]:
    story_bible = _story_bible_from_run(run)
    lint_findings = lint_manuscript(chapters)
    run.current_step = "manuscript_qa"
    record_event(session, run, "manuscript_qa_started", {"message": "Running manuscript QA."})
    session.commit()
    qa_report = _generate_structured_output(
        client,
        run.model_name,
        lambda: build_manuscript_qa_messages(run.project, story_bible, lint_findings, chapters),
        parse_manuscript_qa_report,
        "manuscript QA report",
    )
    if lint_findings:
        merged_findings = list(dict.fromkeys([*qa_report.lint_findings, *lint_findings]))
        qa_report = qa_report.model_copy(update={"lint_findings": merged_findings})
    qa_markdown = render_qa_report_markdown(qa_report)
    return qa_report, qa_markdown


def process_run(session: Session, run: GenerationRun, settings: Settings, client: OllamaClient) -> None:
    _ensure_not_canceled(session, run)

    story_bible = _story_bible_from_run(run) if run.story_bible else _generate_story_bible(session, run, settings, client)

    if not run.outline:
        _generate_outline(session, run, story_bible, client)
        if run.pause_after_outline:
            _pause_for_outline_review(session, run)
            return
    else:
        create_chapters_from_outline(session, run)
        session.commit()

    chapters = _require_requested_chapters(session, run)
    for chapter in chapters:
        if chapter.status == ChapterStatus.COMPLETED and chapter.content and chapter.summary and chapter.continuity_update:
            ledger = _ledger_from_update(ChapterContinuityUpdate.model_validate(chapter.continuity_update))
            run.continuity_ledger = ledger.model_dump()
            _sync_summary_context(run, settings.chapter_summary_window)
            session.commit()
            continue

        _ensure_not_canceled(session, run)
        outline_entry = _outline_entry(run, chapter.chapter_number)
        _draft_chapter(session, run, chapter, outline_entry, story_bible, settings, client)

    completed_chapters = _require_requested_chapters(session, run)
    incomplete_chapters = [
        chapter.chapter_number
        for chapter in completed_chapters
        if chapter.status != ChapterStatus.COMPLETED
        or not chapter.content
        or not chapter.summary
        or not chapter.continuity_update
    ]
    if incomplete_chapters:
        raise RuntimeError(
            "Run finished without fully drafted chapters for: "
            + ", ".join(str(chapter_number) for chapter_number in incomplete_chapters)
        )

    _, qa_markdown = _run_manuscript_qa(session, run, completed_chapters, client)

    run.current_step = "export"
    record_event(session, run, "artifact_export_started", {"message": "Rendering manuscript artifacts."})
    session.commit()

    artifacts = export_run_artifacts(settings.artifacts_dir, run.project, run, completed_chapters, qa_markdown)
    replace_artifacts(session, run, artifacts)
    run.current_step = "completed"
    run.current_chapter = None
    run.status = RunStatus.COMPLETED
    run.completed_at = datetime.utcnow()
    record_event(
        session,
        run,
        "run_completed",
        {"message": "Run finished successfully.", "artifact_count": len(artifacts)},
    )
    session.commit()


def process_run_safe(session: Session, run: GenerationRun, settings: Settings, client: OllamaClient) -> None:
    try:
        process_run(session, run, settings, client)
    except RunCanceled:
        return
    except (OllamaError, OllamaTransportError) as exc:
        if run.current_chapter:
            chapter = next((item for item in run.chapters if item.chapter_number == run.current_chapter), None)
            if chapter is not None:
                chapter.status = ChapterStatus.FAILED
                chapter.error_message = str(exc)
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_failed", {"message": str(exc)})
        session.commit()
    except Exception as exc:
        if run.current_chapter:
            chapter = next((item for item in run.chapters if item.chapter_number == run.current_chapter), None)
            if chapter is not None:
                chapter.status = ChapterStatus.FAILED
                chapter.error_message = str(exc)
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_failed", {"message": str(exc)})
        session.commit()
