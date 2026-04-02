from __future__ import annotations

import time

from .bootstrap import run_migrations
from .logging_utils import configure_logging
from .services.runner import recover_incomplete_runs, run_worker_loop
from .settings import get_settings


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    run_migrations()
    recover_incomplete_runs(settings)
    run_worker_loop(settings)


if __name__ == "__main__":
    run()
