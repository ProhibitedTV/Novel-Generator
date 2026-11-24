"""Keep worker liveness distinct from model progress during blocking calls."""
from contextlib import contextmanager
from datetime import datetime
import logging
from threading import Event, Thread

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from ..models import GenerationRun, RunStatus

logger = logging.getLogger(__name__)


def publish_heartbeat(session_factory, run_id, worker_id, *, now=None):
    """Use a separate short transaction; never update another owner's run."""
    with session_factory() as session:
        result = session.execute(
            update(GenerationRun)
            .where(GenerationRun.id == run_id,
                   GenerationRun.status == RunStatus.RUNNING,
                   GenerationRun.worker_id == worker_id)
            .values(last_heartbeat_at=now or datetime.utcnow(),
                    updated_at=GenerationRun.updated_at)
        )
        session.commit()
        return result.rowcount == 1


@contextmanager
def maintain_worker_heartbeat(session_factory, run_id, worker_id, *, interval_seconds):
    """Pulse while the worker owns the run, including during slow inference.

    No ORM objects or processing session are shared with the heartbeat thread.
    A pulse proves worker liveness, not successful model output or progress.
    """
    stop = Event()

    def pulse():
        while not stop.wait(interval_seconds):
            try:
                if not publish_heartbeat(session_factory, run_id, worker_id):
                    return
            except SQLAlchemyError:
                # A busy database can delay a pulse. Let the next pulse retry;
                # never replace the processing transaction or claim ownership.
                logger.warning("Heartbeat write failed for run %s; will retry", run_id, exc_info=True)

    thread = Thread(target=pulse, name="run-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
