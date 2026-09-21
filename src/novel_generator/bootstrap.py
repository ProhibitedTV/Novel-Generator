from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from .settings import get_settings


def _find_project_root() -> Path:
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parents[2],
        Path(__file__).resolve().parents[1],
    ]
    for candidate in candidates:
        if (candidate / "alembic.ini").exists() and (candidate / "alembic").exists():
            return candidate
    raise FileNotFoundError("Could not locate alembic.ini and the alembic directory.")


def run_migrations() -> None:
    database_url = make_url(get_settings().database_url)
    if database_url.get_backend_name() == "sqlite" and database_url.database not in {None, "", ":memory:"}:
        Path(database_url.database).parent.mkdir(parents=True, exist_ok=True)
    project_root = _find_project_root()
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    command.upgrade(config, "head")
