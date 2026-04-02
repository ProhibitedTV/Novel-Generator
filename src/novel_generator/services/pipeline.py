from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import ChapterStatus, GenerationRun, RunStatus
from ..repositories import create_chapters_from_outline, record_event, replace_artifacts
from ..settings import Settings
from .exports import export_run_artifacts
from .ollama import OllamaClient, OllamaError, OllamaTransportError
from .prompts import (
    build_chapter_draft_messages,
    build_chapter_plan_messages,
    build_outline_messages,
    build_summary_messages,
    parse_outline,
    rolling_context,
)


class RunCanceled(Exception):
    pass


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


def process_run(session: Session, run: GenerationRun, settings: Settings, client: OllamaClient) -> None:
    project = run.project
    _ensure_not_canceled(session, run)

    if not run.outline:
        run.current_step = "outline"
        record_event(session, run, "outline_started", {"message": "Generating outline."})
        session.commit()
        outline_text = client.chat(run.model_name, build_outline_messages(project, run))
        run.outline = parse_outline(outline_text, run.requested_chapters)
        create_chapters_from_outline(session, run)
        record_event(session, run, "outline_completed", {"chapters": len(run.outline or [])})
        session.commit()
    else:
        create_chapters_from_outline(session, run)
        session.commit()

    for chapter in sorted(run.chapters, key=lambda item: item.chapter_number):
        if chapter.status == ChapterStatus.COMPLETED and chapter.content and chapter.summary:
            _sync_summary_context(run, settings.chapter_summary_window)
            session.commit()
            continue

        _ensure_not_canceled(session, run)
        run.current_chapter = chapter.chapter_number
        run.current_step = "chapter_plan"
        _sync_summary_context(run, settings.chapter_summary_window)
        record_event(
            session,
            run,
            "chapter_planning",
            {"chapter_number": chapter.chapter_number, "title": chapter.title},
        )
        session.commit()

        chapter.plan = client.chat(
            run.model_name,
            build_chapter_plan_messages(project, run, chapter, run.summary_context or ""),
        )
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

        chapter.content = client.chat(
            run.model_name,
            build_chapter_draft_messages(project, run, chapter, run.summary_context or ""),
        )
        chapter.word_count = len((chapter.content or "").split())
        chapter.status = ChapterStatus.COMPLETED
        session.commit()

        _ensure_not_canceled(session, run)
        run.current_step = "chapter_summary"
        chapter.summary = client.chat(run.model_name, build_summary_messages(chapter))
        chapter.error_message = None
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

    run.current_step = "export"
    record_event(session, run, "artifact_export_started", {"message": "Rendering manuscript artifacts."})
    session.commit()

    artifacts = export_run_artifacts(settings.artifacts_dir, project, run, list(run.chapters))
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
