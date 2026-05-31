from __future__ import annotations

import logging
import os
import socket
import time

from ..db import build_session_factory
from ..repositories import claim_next_queued_run, ensure_provider_configs, get_run_for_processing, recover_running_runs
from ..settings import Settings
from .pipeline import process_run_safe
from .providers import ProviderManager

logger = logging.getLogger(__name__)


def recover_incomplete_runs(settings: Settings) -> None:
    session_factory = build_session_factory(settings)
    with session_factory() as session:
        count = recover_running_runs(
            session,
            stale_after_seconds=settings.run_stale_after_seconds,
            reason="worker_startup",
        )
        session.commit()
        if count:
            logger.info("Recovered %s interrupted runs.", count)


def run_worker_loop(settings: Settings) -> None:
    session_factory = build_session_factory(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        with session_factory() as session:
            stale_count = recover_running_runs(
                session,
                stale_after_seconds=settings.run_stale_after_seconds,
                reason="stale_heartbeat",
            )
            if stale_count:
                logger.warning("Recovered %s stale running runs.", stale_count)
            run = claim_next_queued_run(session, worker_id=worker_id)
            session.commit()
            if run is None:
                time.sleep(settings.worker_poll_interval_seconds)
                continue
            run_id = run.id

        with session_factory() as session:
            run = get_run_for_processing(session, run_id)
            if run is None:
                continue
            provider_configs = ensure_provider_configs(session, settings)
            session.commit()
            client = ProviderManager(settings, provider_configs)
            try:
                process_run_safe(session, run, settings, client)
            except Exception:
                logger.exception("Worker failed while processing run %s", run_id)
