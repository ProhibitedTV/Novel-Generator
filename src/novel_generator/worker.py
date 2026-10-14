from __future__ import annotations

import time

from .bootstrap import run_migrations
from .logging_utils import configure_logging
from .services.runner import recover_incomplete_runs, run_worker_loop
from .services.worker_lease import exclusive_worker_lease
from .settings import get_settings


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with exclusive_worker_lease(settings) as exclusive_owner:
        run_migrations()
        recover_incomplete_runs(settings, exclusive_owner=exclusive_owner)
        run_worker_loop(settings)


if __name__ == "__main__":
    run()
