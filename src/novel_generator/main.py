from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .bootstrap import run_migrations
from .logging_utils import configure_logging
from .routers import api, ui
from .settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    run_migrations()

    app = FastAPI(title=settings.app_name)
    app.include_router(ui.router)
    app.include_router(api.router, prefix="/api")
    app.mount("/static", StaticFiles(packages=[("novel_generator", "static")]), name="static")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "novel_generator.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
    )
