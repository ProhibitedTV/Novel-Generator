from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import GenerationRun, RunStatus
from ..repositories import record_event


def request_run_cancellation(session: Session, run: GenerationRun) -> GenerationRun:
    if run.status in {RunStatus.QUEUED, RunStatus.AWAITING_APPROVAL}:
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_canceled", {"message": "Run canceled before processing started."})
    elif run.status == RunStatus.RUNNING:
        run.cancel_requested = True
        record_event(session, run, "cancel_requested", {"message": "Cancellation requested."})
    return run


def approve_outline_review(session: Session, run: GenerationRun) -> GenerationRun:
    if run.status != RunStatus.AWAITING_APPROVAL:
        raise ValueError("Only runs waiting for outline approval can be approved.")
    run.status = RunStatus.QUEUED
    run.current_step = "queued"
    run.pause_after_outline = False
    run.cancel_requested = False
    record_event(session, run, "outline_approved", {"message": "Outline approved. Run re-queued for drafting."})
    return run
