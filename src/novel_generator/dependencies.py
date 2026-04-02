from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

from fastapi import Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from .db import build_session_factory
from .settings import Settings, get_settings


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return build_session_factory(get_settings())


def get_db(session_factory: sessionmaker[Session] = Depends(get_session_factory)) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_app_settings() -> Settings:
    return get_settings()
