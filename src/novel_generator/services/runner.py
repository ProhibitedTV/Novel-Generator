from __future__ import annotations

import logging
import os
import socket
import time

from ..db import build_session_factory
from ..repositories import claim_next_queued_run, ensure_provider_configs, get_run_for_processing, recover_running_runs
from ..settings import Settings
from .adaptive_length_runtime import install_adaptive_length_runtime
from .context_runtime import install_context_compiler
from .continuity_lifecycle import install_continuity_lifecycle
from .longform_runtime import install_longform_runtime
from .pipeline import process_run_safe
from .providers import ProviderManager
from .recall_runtime import install_recall_runtime
from .truncation_runtime import install_truncation_runtime

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
    runtime_transforms = install_context_compiler()
    runtime_transforms += install_longform_runtime()
    runtime_transforms += install_recall_runtime()
    runtime_transforms += install_adaptive_length_runtime()
    runtime_transforms += install_continuity_lifecycle()
    # Install after telemetry so continuation attempts inherit the same safe attempt metadata and
    # provider-metric persistence as ordinary calls.
    runtime_transforms += install_truncation_runtime()
    if runtime_transforms:
        logger.info(
            "Installed %s long-form context, pacing, continuity, telemetry, and truncation-recovery runtime transforms.",
            runtime_transforms,
        )

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
