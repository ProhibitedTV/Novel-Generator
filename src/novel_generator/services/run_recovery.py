"""Persisted cooldowns for transient provider outages during unattended runs."""
from datetime import datetime, timedelta

from sqlalchemy import select

from ..models import GenerationRun, RunEvent, RunStatus
from ..repositories import record_event
from .provider_errors import ProviderTransportError


def schedule_provider_retry(session, run, settings, error, *, now=None):
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ProviderTransportError):
            break
        cause = cause.__cause__
    else:
        return False
    if run.quality_profile != "autonomous":
        return False
    session.refresh(run, ["cancel_requested", "status"])
    if run.cancel_requested or run.status == RunStatus.CANCELED:
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        run.completed_at = now or datetime.utcnow()
        run.worker_id = None
        record_event(session, run, "run_canceled", {"message": "Canceled during provider recovery."})
        session.commit()
        return True
    schedules = list(session.scalars(select(RunEvent).where(
        RunEvent.run_id == run.id, RunEvent.event_type == "provider_retry_scheduled")))
    if len(schedules) >= settings.provider_recovery_attempts:
        return False
    if run.current_step == "autonomous_revision":
        latest = session.scalar(select(RunEvent).where(RunEvent.run_id == run.id,
            RunEvent.event_type.in_(["autonomous_repair_started", "autonomous_repair_completed", "autonomous_repair_interrupted"])
        ).order_by(RunEvent.id.desc()).limit(1))
        if latest is not None and latest.event_type == "autonomous_repair_started":
            record_event(session, run, "autonomous_repair_interrupted", {
                "message": "Transport interrupted the unsaved revision; preserve its editorial attempt budget.",
                "started_event_id": latest.id,
            })
    delay = min(900, 30 * 2 ** min(len(schedules), 5))
    due = (now or datetime.utcnow()) + timedelta(seconds=delay)
    step = run.current_step
    run.status = RunStatus.QUEUED
    run.current_step = "provider_wait"
    run.worker_id = None
    run.completed_at = None
    run.error_message = f"Provider temporarily unavailable; automatic retry at {due.isoformat()} UTC. {error}"
    record_event(session, run, "provider_retry_scheduled", {
        "message": run.error_message, "retry_at": due.isoformat(), "attempt": len(schedules) + 1,
        "interrupted_step": step, "chapter_number": run.current_chapter,
    })
    session.commit()
    session.expire(run, ["events"])
    return True


def release_provider_retries(session, *, now=None):
    now = now or datetime.utcnow()
    count = 0
    runs = session.scalars(select(GenerationRun).where(
        GenerationRun.status == RunStatus.QUEUED,
        GenerationRun.current_step == "provider_wait",
        GenerationRun.cancel_requested.is_(False),
    ))
    for run in runs:
        event = session.scalar(select(RunEvent).where(RunEvent.run_id == run.id,
            RunEvent.event_type == "provider_retry_scheduled").order_by(RunEvent.id.desc()).limit(1))
        if event is None:
            continue
        if datetime.fromisoformat(event.payload["retry_at"]) > now:
            continue
        run.current_step = "queued"
        run.error_message = None
        run.recovery_count += 1
        record_event(session, run, "provider_retry_ready", {
            "message": "Provider cooldown ended; resuming automatically from saved work.",
            "attempt": event.payload["attempt"],
        })
        count += 1
    session.flush()
    return count
