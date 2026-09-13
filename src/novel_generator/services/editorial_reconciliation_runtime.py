from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from ..schemas import ChapterContinuityUpdate


_INSTALLED = False
_REVISION_EVENT = "developmental_chapter_revision_completed"
_RECONCILED_EVENT = "developmental_continuity_reconciled"


def _event_sequence(event: Any) -> int:
    try:
        return int(getattr(event, "sequence", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _event_chapter(event: Any) -> int:
    payload = getattr(event, "payload", None) or {}
    try:
        return int(payload.get("chapter_number", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def pending_reconciliation_chapters(run: Any) -> list[int]:
    """Return structurally revised chapters whose latest revision lacks a later reconciliation."""

    latest_revision: dict[int, int] = {}
    latest_reconciled: dict[int, int] = {}
    for event in list(getattr(run, "events", None) or []):
        chapter_number = _event_chapter(event)
        if chapter_number <= 0:
            continue
        sequence = _event_sequence(event)
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type == _REVISION_EVENT:
            latest_revision[chapter_number] = max(sequence, latest_revision.get(chapter_number, -1))
        elif event_type == _RECONCILED_EVENT:
            latest_reconciled[chapter_number] = max(sequence, latest_reconciled.get(chapter_number, -1))
    return sorted(
        chapter_number
        for chapter_number, revision_sequence in latest_revision.items()
        if revision_sequence > latest_reconciled.get(chapter_number, -1)
    )


def _record(pipeline: Any, session: Any, run: Any, event_type: str, payload: dict[str, Any]) -> None:
    recorder = getattr(pipeline, "record_event", None)
    if callable(recorder):
        recorder(session, run, event_type, payload)


def reconcile_developmental_continuity(
    session: Any,
    run: Any,
    chapters: list[Any],
    client: Any,
    *,
    pipeline_module: Any | None = None,
) -> list[int]:
    """Refresh changed chapter summaries/continuity and replay the durable ledger in book order.

    Developmental revision is allowed to sharpen permanent consequences. The original continuity
    checkpoint therefore becomes potentially stale. Only chapters that actually completed a
    developmental revision incur new model calls; unchanged downstream chapters reuse their saved
    continuity checkpoints while the ledger is replayed in order.
    """

    if pipeline_module is None:
        from . import pipeline as pipeline_module
    pipeline = pipeline_module

    pending = set(pending_reconciliation_chapters(run))
    if not pending:
        return []

    ordered = sorted(chapters, key=lambda chapter: int(getattr(chapter, "chapter_number", 0) or 0))
    story_bible = pipeline._story_bible_from_run(run)
    ledger = pipeline._build_initial_ledger(story_bible)
    completed: list[int] = []

    _record(
        pipeline,
        session,
        run,
        "developmental_continuity_reconciliation_started",
        {
            "message": "Refreshing continuity checkpoints changed by developmental revision and replaying the book ledger.",
            "chapters": sorted(pending),
        },
    )
    session.commit()

    for chapter in ordered:
        chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
        outline_entry = pipeline._outline_entry(run, chapter_number)
        continuity_update: ChapterContinuityUpdate | None = None

        if chapter_number in pending:
            old_summary = str(getattr(chapter, "summary", "") or "")
            old_update = getattr(chapter, "continuity_update", None)
            summary_refreshed = False
            continuity_refreshed = False

            try:
                provider_name, model_name = pipeline._resolve_stage_route(client, run, "chapter_summary")
                refreshed_summary = pipeline._supervised_provider_chat(
                    session,
                    run,
                    client,
                    provider_name,
                    model_name,
                    pipeline.build_summary_messages(chapter, outline_entry),
                    stage="chapter_summary",
                    chapter_number=chapter_number,
                    metadata={
                        "label": f"chapter {chapter_number} post-developmental summary reconciliation",
                        "phase": "editorial_reconciliation",
                    },
                ).strip()
                if refreshed_summary:
                    chapter.summary = refreshed_summary
                    summary_refreshed = True
                else:
                    chapter.summary = old_summary
            except Exception as exc:
                chapter.summary = old_summary
                _record(
                    pipeline,
                    session,
                    run,
                    "developmental_summary_reconciliation_fallback",
                    {
                        "message": f"Could not refresh chapter {chapter_number} summary after developmental revision; keeping the saved summary.",
                        "chapter_number": chapter_number,
                        "error": str(exc),
                    },
                )
                session.commit()

            try:
                provider_name, model_name = pipeline._resolve_stage_route(client, run, "continuity_update")
                continuity_update = pipeline._generate_structured_output(
                    session,
                    run,
                    client,
                    provider_name,
                    model_name,
                    lambda: pipeline.build_continuity_update_messages(
                        run.project,
                        chapter,
                        ledger,
                        story_bible,
                    ),
                    pipeline.parse_continuity_update,
                    f"chapter {chapter_number} post-developmental continuity reconciliation",
                    "continuity_update",
                    chapter_number,
                )
                continuity_refreshed = True
            except Exception as exc:
                if old_update:
                    continuity_update = ChapterContinuityUpdate.model_validate(old_update)
                _record(
                    pipeline,
                    session,
                    run,
                    "developmental_continuity_reconciliation_fallback",
                    {
                        "message": f"Could not refresh chapter {chapter_number} continuity after developmental revision; replaying the saved checkpoint.",
                        "chapter_number": chapter_number,
                        "error": str(exc),
                    },
                )
                session.commit()

            if continuity_update is None:
                raise RuntimeError(
                    f"Chapter {chapter_number} has no usable continuity checkpoint after developmental revision."
                )

            ledger_after = pipeline._ledger_from_update(ledger, continuity_update)
            ledger_after = pipeline._ledger_with_chapter_mode(
                ledger_after,
                chapter_number,
                getattr(outline_entry, "chapter_mode", ""),
            )
            if continuity_update.timeline != ledger_after.timeline:
                continuity_update.timeline = ledger_after.timeline
            pipeline._apply_continuity_canon_warnings(chapter, continuity_update)
            chapter.continuity_update = continuity_update.model_dump()
            ledger = ledger_after

            if continuity_refreshed:
                _record(
                    pipeline,
                    session,
                    run,
                    _RECONCILED_EVENT,
                    {
                        "message": f"Refreshed chapter {chapter_number} continuity after developmental revision.",
                        "chapter_number": chapter_number,
                        "summary_refreshed": summary_refreshed,
                        "continuity_refreshed": True,
                    },
                )
                completed.append(chapter_number)
                session.commit()
            continue

        stored = getattr(chapter, "continuity_update", None)
        if not stored:
            continue
        continuity_update = ChapterContinuityUpdate.model_validate(stored)
        ledger = pipeline._ledger_from_update(ledger, continuity_update)
        ledger = pipeline._ledger_with_chapter_mode(
            ledger,
            chapter_number,
            getattr(outline_entry, "chapter_mode", ""),
        )

    run.continuity_ledger = ledger.model_dump()
    _record(
        pipeline,
        session,
        run,
        "developmental_continuity_reconciliation_completed",
        {
            "message": "Replayed the continuity ledger after developmental revisions.",
            "reconciled_chapters": completed,
            "fallback_chapters": sorted(pending.difference(completed)),
        },
    )
    session.commit()
    return completed


def _wrap_developmental_revision_waves(
    waves: Callable[..., None],
    *,
    reconcile: Callable[..., list[int]] = reconcile_developmental_continuity,
) -> Callable[..., None]:
    signature = inspect.signature(waves)

    @functools.wraps(waves)
    def wrapped(*args: Any, **kwargs: Any) -> None:
        waves(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            reconcile(
                bound.arguments["session"],
                bound.arguments["run"],
                list(bound.arguments.get("chapters") or []),
                bound.arguments["client"],
            )
        except Exception:
            # The normal pipeline's final QA now sees literal final prose and ending-debt evidence.
            # Reconciliation is valuable but should not throw away a successfully revised manuscript.
            return

    setattr(wrapped, "_novel_editorial_reconciliation_wrapped", True)
    return wrapped


def install_editorial_reconciliation_runtime() -> int:
    """Refresh stale continuity state after structural developmental rewrites."""

    global _INSTALLED
    if _INSTALLED:
        return 0

    from . import pipeline

    waves = getattr(pipeline, "_run_developmental_revision_waves", None)
    if waves is None or getattr(waves, "_novel_editorial_reconciliation_wrapped", False):
        _INSTALLED = True
        return 0

    setattr(pipeline, "_run_developmental_revision_waves", _wrap_developmental_revision_waves(waves))
    _INSTALLED = True
    return 1
