"""Bounded dashboard snapshots. Never load manuscript prose or complete event history."""
from sqlalchemy import func, or_, select

from ..models import Artifact, ChapterDraft, GenerationRun, RunEvent
from .run_diagnostics import safe_message

EVENT_WINDOW = 200
EVENT_KEYS = ("message", "title", "chapter_number", "provider_name", "model_name")


def compact_event_payload(payload):
    result = {}
    for key in EVENT_KEYS:
        value = (payload or {}).get(key)
        if isinstance(value, (str, int, float, bool)):
            result[key] = safe_message(value)[:2000] if isinstance(value, str) else value
    return result


def event_summary(row):
    return {
        "sequence": row.sequence,
        "event_type": row.event_type,
        "payload": compact_event_payload(row.payload),
        "created_at": row.created_at.isoformat(),
    }


def event_history(session, run_id, *, before=None, limit=EVENT_WINDOW):
    query = select(RunEvent.sequence, RunEvent.event_type, RunEvent.payload, RunEvent.created_at).where(
        RunEvent.run_id == run_id)
    if before is not None:
        query = query.where(RunEvent.sequence < before)
    rows = session.execute(query.order_by(RunEvent.sequence.desc()).limit(limit + 1)).all()
    return {
        "events": [event_summary(row) for row in reversed(rows[:limit])],
        "next_before": rows[limit - 1].sequence if len(rows) > limit else None,
    }


def dashboard_snapshot(session, run_id):
    run = session.execute(select(
        GenerationRun.id, GenerationRun.status, GenerationRun.current_step,
        GenerationRun.current_chapter, GenerationRun.provider_name, GenerationRun.model_name,
        GenerationRun.target_word_count, GenerationRun.requested_chapters,
        GenerationRun.started_at, GenerationRun.completed_at, GenerationRun.error_message,
        GenerationRun.outline, GenerationRun.continuity_ledger,
    ).where(GenerationRun.id == run_id)).one_or_none()
    if run is None:
        return None
    chapters = session.execute(select(
        ChapterDraft.chapter_number, ChapterDraft.title, ChapterDraft.status,
        ChapterDraft.word_count, ChapterDraft.outline_summary, ChapterDraft.plan,
        ChapterDraft.qa_notes, ChapterDraft.continuity_update,
        func.length(ChapterDraft.content).label("content_length"),
        func.length(ChapterDraft.summary).label("summary_length"),
    ).where(ChapterDraft.run_id == run_id).order_by(ChapterDraft.chapter_number)).all()

    def event_query():
        return select(RunEvent.sequence, RunEvent.event_type, RunEvent.payload, RunEvent.created_at).where(
            RunEvent.run_id == run_id).order_by(RunEvent.sequence.desc())

    latest = session.execute(event_query().limit(1)).first()
    route = session.execute(event_query().where(or_(
        RunEvent.payload["provider_name"].as_string().is_not(None),
        RunEvent.payload["model_name"].as_string().is_not(None),
    )).limit(1)).first()
    fallbacks = session.execute(event_query().where(
        RunEvent.event_type.contains("fallback")).limit(4)).all()
    event_count = session.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run_id))
    artifact_count = session.scalar(select(func.count()).select_from(Artifact).where(Artifact.run_id == run_id))
    return {
        "id": run.id, "status": run.status.value, "current_step": run.current_step,
        "current_chapter": run.current_chapter, "provider_name": run.provider_name,
        "model_name": run.model_name, "target_word_count": run.target_word_count,
        "requested_chapters": run.requested_chapters,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": safe_message(run.error_message),
        "outline": run.outline or [], "continuity_ledger": run.continuity_ledger or {},
        "chapters": [{
            "chapter_number": row.chapter_number, "title": row.title, "status": row.status.value,
            "word_count": row.word_count, "outline_summary": row.outline_summary, "plan": row.plan,
            "qa_notes": row.qa_notes, "continuity_update": row.continuity_update,
            "has_content": bool(row.content_length), "has_summary": bool(row.summary_length),
        } for row in chapters],
        "latest_event": event_summary(latest) if latest else None,
        "active_route": compact_event_payload(route.payload) if route else {},
        "fallback_events": [event_summary(row) for row in reversed(fallbacks)],
        "event_count": event_count, "artifact_count": artifact_count,
    }
