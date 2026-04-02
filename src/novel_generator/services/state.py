from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import GenerationRun, RunStatus
from ..repositories import record_event


def request_run_cancellation(session: Session, run: GenerationRun) -> GenerationRun:
    if run.status == RunStatus.QUEUED:
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_canceled", {"message": "Run canceled before processing started."})
    elif run.status == RunStatus.RUNNING:
        run.cancel_requested = True
        record_event(session, run, "cancel_requested", {"message": "Cancellation requested."})
    return run
