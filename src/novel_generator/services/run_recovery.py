"""Persisted cooldowns for transient provider outages during unattended runs."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import GenerationRun, RunEvent, RunStageAttempt, RunStatus
from ..repositories import record_event, interrupt_unsaved_editorial_repair
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
    # A multi-day book can survive many unrelated outages. Budget and backoff
    # apply to consecutive failures since actual inference last succeeded,
    # rather than exhausting a lifetime allowance despite useful progress.
    last_success = session.scalar(select(RunStageAttempt.completed_at).where(
        RunStageAttempt.run_id == run.id, RunStageAttempt.status == "success",
        RunStageAttempt.completed_at.is_not(None),
    ).order_by(RunStageAttempt.completed_at.desc()).limit(1))
    # Explicitly resuming a failed run authorizes a fresh provider outage
    # window. It must not reset any content-quality or editorial budgets.
    manual_resume = session.scalar(select(func.max(RunEvent.sequence)).where(
        RunEvent.run_id == run.id, RunEvent.event_type == "run_resume_queued"))
    schedules_query = select(func.count()).select_from(RunEvent).where(
        RunEvent.run_id == run.id, RunEvent.event_type == "provider_retry_scheduled")
    if last_success is not None:
        schedules_query = schedules_query.where(RunEvent.created_at >= last_success)
    if manual_resume is not None:
        schedules_query = schedules_query.where(RunEvent.sequence > manual_resume)
    attempts = session.scalar(schedules_query) or 0
    if run.current_step == "autonomous_revision":
        interrupt_unsaved_editorial_repair(session, run, reason="provider_transport")
    if attempts >= settings.provider_recovery_attempts:
        record_event(session, run, "provider_retry_exhausted", {
            "message": "Automatic provider retry budget exhausted. Restore provider connectivity and resume the saved run.",
            "attempts": attempts, "limit": settings.provider_recovery_attempts,
            "interrupted_step": run.current_step, "chapter_number": run.current_chapter,
        })
        # The terminal failure handler commits the evidence and preserves prose.
        return False
    delay = min(900, 30 * 2 ** min(attempts, 5))
    due = (now or datetime.utcnow()) + timedelta(seconds=delay)
    step = run.current_step
    run.status = RunStatus.QUEUED
    run.current_step = "provider_wait"
    run.worker_id = None
    run.completed_at = None
    run.error_message = f"Provider temporarily unavailable; automatic retry at {due.isoformat()} UTC. {error}"
    record_event(session, run, "provider_retry_scheduled", {
        "message": run.error_message, "retry_at": due.isoformat(), "attempt": attempts + 1,
        "limit": settings.provider_recovery_attempts,
        "interrupted_step": step, "chapter_number": run.current_chapter,
        "last_successful_inference_at": last_success.isoformat() if last_success else None,
    })
    session.commit()
    session.expire(run, ["events"])
    return True


def release_provider_retries(session, *, now=None):
    now = now or datetime.utcnow()
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    count = 0
    runs = session.scalars(select(GenerationRun).where(
        GenerationRun.status == RunStatus.QUEUED,
        GenerationRun.current_step == "provider_wait",
        GenerationRun.cancel_requested.is_(False),
    ))
    for run in runs:
        event = session.scalar(select(RunEvent).where(RunEvent.run_id == run.id,
            RunEvent.event_type == "provider_retry_scheduled").order_by(RunEvent.id.desc()).limit(1))
        try:
            if event is None:
                raise ValueError("Missing retry schedule")
            due = datetime.fromisoformat(event.payload["retry_at"])
            if due.tzinfo is not None:
                due = due.astimezone(timezone.utc).replace(tzinfo=None)
            if not isinstance(event.payload.get("attempt"), int):
                raise ValueError("Missing retry attempt number")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            # A malformed persisted schedule cannot safely auto-resume. Fail
            # this run with diagnostics instead of crashing the entire worker.
            run.status = RunStatus.FAILED
            run.current_step = "failed"
            run.worker_id = None
            run.completed_at = now
            run.error_message = f"Provider retry schedule is invalid: {exc}. Resume the saved run to retry."
            record_event(session, run, "provider_retry_invalid", {
                "message": run.error_message, "failed_step": "provider_wait",
            })
            continue
        if due > now:
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
