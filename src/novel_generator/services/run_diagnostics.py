"""Small, read-only operational snapshots, independent of model inference."""
from datetime import datetime, timezone
import re

from sqlalchemy import select

from ..models import GenerationRun, RunEvent, RunStageAttempt


def safe_message(value):
    # Best-effort credential redaction. Errors may still contain private text.
    text = str(value or "")
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"""(?i)((?:api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*)["']?[^\s,;"'}]+""", r"\1[redacted]", text)
    return text[:12000] + ("\n[error shortened]" if len(text) > 12000 else "")


def failure_context(run, exc):
    causes = []
    seen = set()
    cause = exc
    while cause is not None and id(cause) not in seen and len(causes) < 5:
        seen.add(id(cause))
        causes.append({"type": type(cause).__name__, "message": safe_message(cause)})
        cause = cause.__cause__
    return {"message": safe_message(exc), "failed_step": run.current_step,
            "chapter_number": run.current_chapter, "error_type": type(exc).__name__, "causes": causes}


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def read_status(session, run_id, *, stale_after_seconds):
    row = session.execute(select(
        GenerationRun.id, GenerationRun.status, GenerationRun.current_step,
        GenerationRun.current_chapter, GenerationRun.provider_name, GenerationRun.model_name,
        GenerationRun.quality_profile, GenerationRun.last_heartbeat_at, GenerationRun.worker_id,
        GenerationRun.recovery_count, GenerationRun.error_message, GenerationRun.cancel_requested,
    ).where(GenerationRun.id == run_id)).one_or_none()
    if row is None:
        return None
    heartbeat = _utc(row.last_heartbeat_at)
    age = max(0, int((datetime.now(timezone.utc) - heartbeat).total_seconds())) if heartbeat else None
    latest = session.execute(select(
        RunStageAttempt.stage, RunStageAttempt.chapter_number, RunStageAttempt.provider_name,
        RunStageAttempt.model_name, RunStageAttempt.status, RunStageAttempt.started_at,
    ).where(RunStageAttempt.run_id == run_id)
      .order_by(RunStageAttempt.id.desc()).limit(1)).one_or_none()
    retry_at = None
    if row.current_step == "provider_wait":
        retry = session.scalar(select(RunEvent.payload).where(
            RunEvent.run_id == run_id, RunEvent.event_type == "provider_retry_scheduled",
        ).order_by(RunEvent.sequence.desc()).limit(1))
        retry_at = (retry or {}).get("retry_at")
    return {
        "run_id": row.id, "status": row.status.value, "step": row.current_step,
        "chapter": row.current_chapter, "provider": row.provider_name, "model": row.model_name,
        "profile": row.quality_profile, "worker_id": row.worker_id,
        "heartbeat_at": heartbeat.isoformat() if heartbeat else None, "heartbeat_age_seconds": age,
        "heartbeat_stale": row.status.value == "running" and (age is None or age >= stale_after_seconds),
        "recovery_count": row.recovery_count, "error": safe_message(row.error_message),
        "cancel_requested": row.cancel_requested, "retry_at_utc": retry_at,
        "latest_attempt": None if latest is None else {
            "stage": latest.stage, "chapter": latest.chapter_number, "provider": latest.provider_name,
            "model": latest.model_name, "status": latest.status, "started_at": _utc(latest.started_at).isoformat(),
        },
    }


def diagnostics_text(session, run_id, *, stale_after_seconds):
    snapshot = read_status(session, run_id, stale_after_seconds=stale_after_seconds)
    if snapshot is None:
        return None
    lines = ["Novel Generator — run diagnostics", "Review before sharing: errors may include private text.", ""]
    for key, value in snapshot.items():
        lines.append(f"{key}: {value if value is not None else '-'}")
    events = session.execute(select(RunEvent.sequence, RunEvent.event_type, RunEvent.payload).where(
        RunEvent.run_id == run_id, RunEvent.event_type.in_([
            "run_failed", "outline_validation_rejected", "outline_batch_rejected",
            "provider_retry_scheduled", "provider_retry_exhausted", "provider_retry_invalid",
            "run_requeued", "outline_pacing_replan_requested", "autonomous_repair_interrupted",
        ]),
    ).order_by(RunEvent.sequence.desc()).limit(8)).all()
    lines += ["", "Recent failure and recovery events (newest first):"]
    for event in events:
        lines.append(f"#{event.sequence} {event.event_type}")
        payload = event.payload or {}
        for key in ("failed_step", "interrupted_step", "chapter_number", "error_type", "message",
                    "error", "repair_error", "retry_at", "attempt", "attempts", "limit", "reason"):
            if payload.get(key) is not None:
                lines.append(f"  {key}: {safe_message(payload[key])}")
        for cause in (payload.get("causes") or [])[:5]:
            lines.append(f"  cause {safe_message(cause.get('type'))}: {safe_message(cause.get('message'))}")
    attempts = session.execute(select(
        RunStageAttempt.stage, RunStageAttempt.chapter_number, RunStageAttempt.provider_name,
        RunStageAttempt.model_name, RunStageAttempt.status, RunStageAttempt.error_type,
        RunStageAttempt.error_message,
    ).where(RunStageAttempt.run_id == run_id, RunStageAttempt.status.in_(["failed", "interrupted"]))
      .order_by(RunStageAttempt.id.desc()).limit(8)).all()
    lines += ["", "Recent failed/interrupted inference attempts (newest first):"]
    for attempt in attempts:
        lines.append(f"{attempt.stage} / chapter {attempt.chapter_number or '-'} / {attempt.provider_name} / {attempt.model_name} / {attempt.status}")
        lines.append(f"  {safe_message(attempt.error_type)}: {safe_message(attempt.error_message)}")
    return "\n".join(lines) + "\n"
