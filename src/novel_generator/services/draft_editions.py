"""Immutable author/editor handoff editions, separate from publication artifacts."""
from hashlib import sha256
from pathlib import Path
import json
import os
import tempfile

from sqlalchemy import select

from ..models import RunEvent
from ..repositories import record_event

EDITION_LABELS = {
    "first-draft": "Complete first draft",
    "structural-draft": "Post-developmental draft",
}
LINE_STAGE_EVENTS = {
    "publication_humanization_started", "publication_humanization_completed",
    "publication_compression_started", "publication_compression_completed",
    "final_editing_started", "final_chapter_edit_completed", "autonomous_quality_passed",
}
STRUCTURAL_STAGE_EVENTS = LINE_STAGE_EVENTS | {
    "developmental_revision_started", "developmental_chapter_revision_completed",
}


def _digest(data):
    return sha256(data).hexdigest()


def edition_event(session, run_id, stage):
    return session.scalar(select(RunEvent).where(
        RunEvent.run_id == run_id,
        RunEvent.event_type == "draft_edition_saved",
        RunEvent.payload["stage"].as_string() == stage,
    ).order_by(RunEvent.sequence.asc()).limit(1))


def edition_path(artifacts_dir, run_id, stage):
    if stage not in EDITION_LABELS:
        raise ValueError("Unknown draft edition")
    return Path(artifacts_dir) / run_id / "editorial-history" / (stage + ".md")


def save_draft_edition(session, run, chapters, artifacts_dir, stage):
    """Capture each milestone once; never relabel a later legacy revision.

    This is an editorial working copy, not a quality verdict. The exact source
    prose is preserved without export sanitization. No inference is required.
    """
    existing = edition_event(session, run.id, stage)
    if existing is not None:
        return existing.payload
    later_events = STRUCTURAL_STAGE_EVENTS if stage == "first-draft" else LINE_STAGE_EVENTS
    if any(event.event_type in later_events for event in run.events):
        return None
    ordered = sorted(chapters, key=lambda c: c.chapter_number)
    if [c.chapter_number for c in ordered] != list(range(1, run.requested_chapters + 1)):
        return None
    if not all((c.content or "").strip() for c in ordered):
        return None
    path = edition_path(artifacts_dir, run.id, stage)
    lines = [f"# {run.project.title}", "", f"**{EDITION_LABELS[stage]} — WORKING EDITION, NOT A PUBLICATION EXPORT**", "",
             "Preserved for revision comparison. This edition does not establish manuscript acceptance.", ""]
    chapter_hashes = {}
    for chapter in ordered:
        chapter_hashes[str(chapter.chapter_number)] = _digest(chapter.content.encode("utf-8"))
        lines.extend([f"## Chapter {chapter.chapter_number}: {chapter.title}", "", chapter.content, ""])
    data = "\n".join(lines).encode("utf-8")
    checksum = _digest(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement of a new snapshot; never overwrite a previously
    # saved edition. A file left before its DB event can be recovered only
    # if it exactly matches the currently committed source.
    if path.exists():
        if _digest(path.read_bytes()) != checksum:
            raise RuntimeError(f"Unregistered {stage} edition differs from saved source; preserved both manuscript and edition.")
    else:
        fd, temp = tempfile.mkstemp(prefix=stage + "-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    payload = {"stage": stage, "message": f"{EDITION_LABELS[stage]} archived for editorial comparison.",
               "sha256": checksum, "chapter_hashes": chapter_hashes,
               "words": sum(len(c.content.split()) for c in ordered),
               "chapters": len(ordered)}
    record_event(session, run, "draft_edition_saved", payload)
    session.commit()
    return payload
