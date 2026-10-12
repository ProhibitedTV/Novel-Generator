"""Keep malformed continuity output available without replacing saved story state."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..models import GenerationRun


def record_rejected_continuity(session, run, client, raw, error, chapter_number, correction):
    if not isinstance(session, Session) or not isinstance(run, GenerationRun):
        return
    from ..repositories import record_event
    from ..settings import get_settings
    from .autonomous_editorial import _hash
    config = getattr(client, "settings", None) or get_settings()
    directory = Path(config.artifacts_dir) / run.id / "structured-output-rejections"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"continuity-{chapter_number or 0}-{_hash(raw)[:16]}.json"
    feedback = (error.errors(include_input=False, include_url=False, include_context=False)
                if isinstance(error, ValidationError) else str(error))
    payload = {"stage": "continuity_update", "chapter_number": chapter_number,
               "correction": correction, "validation_error": feedback, "raw_output": raw}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)
    record_event(session, run, "continuity_output_rejected", {
        "message": "Rejected incomplete or invalid continuity output; saved source and prior story state remain intact.",
        "chapter_number": chapter_number, "correction": correction,
        "validation_error": feedback, "diagnostic_path": path.relative_to(config.artifacts_dir).as_posix()})
    session.commit()
    session.expire(run, ["events"])
