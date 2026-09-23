"""Small, resumable outline requests with bounded subdivision on invalid output."""
from __future__ import annotations

import json

from .autonomous_editorial import AutonomousQualityError, _hash, _record, _events
from .prompts import build_outline_chunk_messages, parse_outline_chunk


AUTONOMOUS_OUTLINE_BATCH_SIZE = 4


def generate_outline(session, run, bible, client, provider, model):
    from . import pipeline

    outline = []
    identity = {
        "contract": 1, "bible": bible.model_dump(), "brief": run.project.story_brief,
        "premise": run.project.premise, "chapters": run.requested_chapters,
        "target_words": run.target_word_count, "minimum": run.min_words_per_chapter,
        "maximum": run.max_words_per_chapter, "provider": provider, "model": model,
    }

    def generate_range(start, end):
        pipeline._ensure_not_canceled(session, run)
        fingerprint = _hash({**identity, "start": start, "end": end, "prior": outline})
        for saved in reversed(_events(run, "outline_batch_checkpoint")):
            if saved.get("fingerprint") == fingerprint:
                # Checkpoints are validated again, never trusted as opaque JSON.
                chunk = parse_outline_chunk(json.dumps({"chapters": saved["outline"]}), start, end)
                outline.extend(chunk)
                return
        split = any(saved.get("fingerprint") == fingerprint for saved in _events(run, "outline_batch_split"))
        if not split:
            _record(session, run, "outline_chunk_started", {
                "message": f"Generating outline chapters {start}-{end}.",
                "start_chapter": start, "end_chapter": end,
            })
            try:
                chunk = pipeline._generate_structured_output(
                    session, run, client, provider, model,
                    lambda: build_outline_chunk_messages(run.project, run, bible,
                        start_chapter=start, end_chapter=end, prior_outline=list(outline)),
                    lambda raw: parse_outline_chunk(raw, start, end),
                    f"structured outline chapters {start}-{end}", "outline_chunk", None,
                )
            except ValueError as exc:
                pipeline._ensure_not_canceled(session, run)
                _record(session, run, "outline_batch_rejected", {
                    "message": f"Outline chapters {start}-{end} failed validation.",
                    "start_chapter": start, "end_chapter": end, "error": str(exc),
                })
                if start == end:
                    raise AutonomousQualityError(f"Outline chapter {start} failed validation after repair: {exc}") from exc
                _record(session, run, "outline_batch_split", {
                    "message": f"Retrying outline chapters {start}-{end} in smaller batches.",
                    "fingerprint": fingerprint, "start_chapter": start, "end_chapter": end,
                })
            else:
                _record(session, run, "outline_batch_checkpoint", {
                    "message": f"Saved validated outline chapters {start}-{end}.",
                    "fingerprint": fingerprint, "start_chapter": start, "end_chapter": end,
                    "outline": chunk,
                })
                outline.extend(chunk)
                return
        midpoint = (start + end) // 2
        generate_range(start, midpoint)
        generate_range(midpoint + 1, end)

    for start in range(1, run.requested_chapters + 1, AUTONOMOUS_OUTLINE_BATCH_SIZE):
        generate_range(start, min(run.requested_chapters, start + AUTONOMOUS_OUTLINE_BATCH_SIZE - 1))
    return outline
