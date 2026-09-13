from __future__ import annotations

import functools
import re
from typing import Any, Callable

from ..schemas import ChapterContinuityUpdate, ContinuityLedger


_INSTALLED = False


def _normalized(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _thread_is_resolved(thread: str, resolved_threads: list[str]) -> bool:
    candidate = _normalized(thread)
    if not candidate:
        return False
    for resolved in resolved_threads:
        closed = _normalized(resolved)
        if not closed:
            continue
        if candidate == closed:
            return True
        # Models sometimes rephrase a resolution slightly. Containment is only accepted for
        # substantive phrases so generic words cannot accidentally close unrelated threads.
        if min(len(candidate), len(closed)) >= 24 and (candidate in closed or closed in candidate):
            return True
    return False


def apply_continuity_lifecycle(
    merged: ContinuityLedger,
    update: ChapterContinuityUpdate,
) -> ContinuityLedger:
    """Apply snapshot semantics to live continuity fields after the historical merge.

    The continuity prompt asks the model for the *currently open/still-live* state after a chapter.
    Several of these fields were historically merged append-only, which made healed trust fractures,
    resolved emotional loops, closed promises, and resolved threads impossible to remove. Historical
    fields (timeline, resolved_threads, transitions, decisions) remain cumulative; live fields use the
    update snapshot.
    """

    resolved = list(merged.resolved_threads)
    open_threads = [
        thread
        for thread in update.open_threads
        if thread and not _thread_is_resolved(thread, resolved)
    ]

    return merged.model_copy(
        update={
            "open_threads": list(dict.fromkeys(open_threads)),
            "open_promises_by_name": dict(update.open_promises_by_name),
            "memory_damage": dict(update.memory_damage),
            "trust_fractures": dict(update.trust_fractures),
            "civilian_pressure_points": list(dict.fromkeys(update.civilian_pressure_points)),
            "emotional_open_loops": dict(update.emotional_open_loops),
        }
    )


def _wrap_ledger_merge(
    merge: Callable[[ContinuityLedger, ChapterContinuityUpdate], ContinuityLedger],
) -> Callable[[ContinuityLedger, ChapterContinuityUpdate], ContinuityLedger]:
    @functools.wraps(merge)
    def wrapped(current_ledger: ContinuityLedger, update: ChapterContinuityUpdate) -> ContinuityLedger:
        merged = merge(current_ledger, update)
        return apply_continuity_lifecycle(merged, update)

    setattr(wrapped, "_novel_continuity_lifecycle_wrapped", True)
    return wrapped


def install_continuity_lifecycle() -> int:
    """Make live continuity fields truly resolvable while preserving historical state."""

    global _INSTALLED
    if _INSTALLED:
        return 0

    from . import pipeline

    merge = getattr(pipeline, "_ledger_from_update", None)
    if merge is None or getattr(merge, "_novel_continuity_lifecycle_wrapped", False):
        _INSTALLED = True
        return 0

    setattr(pipeline, "_ledger_from_update", _wrap_ledger_merge(merge))
    _INSTALLED = True
    return 1
