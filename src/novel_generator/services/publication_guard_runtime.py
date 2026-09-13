from __future__ import annotations

import functools
import inspect
import re
from typing import Any, Callable

from .ending_debt import compile_ending_debt_audit


_INSTALLED = False
_INTENTIONAL_CLOSURE_MARKERS = (
    "intentional aftermath",
    "intentional sequel",
    "intentionally unresolved",
    "deliberate sequel",
    "deliberately unresolved",
    "purposefully left open",
    "resolved on page",
    "paid off on page",
    "transformed into aftermath",
)
_NEGATION_RE = re.compile(r"\b(?:not|never|isn't|wasn't|isnt|wasnt|without)\b", re.IGNORECASE)


def _word_count(chapter: Any) -> int:
    stored = getattr(chapter, "word_count", None)
    try:
        value = int(stored or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    return len(str(getattr(chapter, "content", "") or "").split())


def _chapters(run: Any) -> list[Any]:
    return sorted(
        list(getattr(run, "chapters", None) or []),
        key=lambda chapter: int(getattr(chapter, "chapter_number", 0) or 0),
    )


def _positive_ending_classification(qa_report: Any) -> bool:
    notes = list(getattr(qa_report, "ending_coherence_notes", None) or [])
    for note in notes:
        lowered = " ".join(str(note or "").lower().split())
        if not lowered:
            continue
        for marker in _INTENTIONAL_CLOSURE_MARKERS:
            start = lowered.find(marker)
            if start < 0:
                continue
            prefix = lowered[max(0, start - 32) : start]
            if not _NEGATION_RE.search(prefix):
                return True
    return False


def compile_publication_blockers(run: Any, qa_report: Any) -> list[str]:
    """Return deterministic conditions that should veto a publication-ready label.

    The guard intentionally does not fail the run. It only prevents a finished manuscript from being
    mislabeled publication-ready when book-scale requirements are visibly unmet.
    """

    chapters = _chapters(run)
    blockers: list[str] = []
    if not chapters:
        return ["No completed chapters were available to the publication-readiness guard."]

    total_words = sum(_word_count(chapter) for chapter in chapters)
    try:
        target_words = int(getattr(run, "target_word_count", 0) or 0)
    except (TypeError, ValueError):
        target_words = 0
    if target_words > 0:
        ratio = total_words / target_words
        if ratio < 0.90:
            blockers.append(
                f"Final manuscript is materially under target at {total_words:,} words versus {target_words:,} ({ratio:.0%})."
            )
        elif ratio > 1.15:
            blockers.append(
                f"Final manuscript is materially over target at {total_words:,} words versus {target_words:,} ({ratio:.0%})."
            )

    try:
        minimum = int(getattr(run, "min_words_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        minimum = 0
    try:
        maximum = int(getattr(run, "max_words_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        maximum = 0

    if minimum > 0:
        severe_low = [
            int(getattr(chapter, "chapter_number", 0) or 0)
            for chapter in chapters
            if _word_count(chapter) < max(1, int(minimum * 0.85))
        ]
        if severe_low:
            blockers.append(
                "Severely under-length final chapter(s): " + ", ".join(str(number) for number in severe_low[:12]) + "."
            )
    if maximum > 0:
        severe_high = [
            int(getattr(chapter, "chapter_number", 0) or 0)
            for chapter in chapters
            if _word_count(chapter) > int(maximum * 1.25)
        ]
        if severe_high:
            blockers.append(
                "Severely over-length final chapter(s): " + ", ".join(str(number) for number in severe_high[:12]) + "."
            )

    debt = compile_ending_debt_audit(run, chapters).payload
    meta = debt.get("_ending_debt_audit", {}) if isinstance(debt, dict) else {}
    try:
        central_count = int(meta.get("central_candidate_count", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        central_count = 0
    if central_count > 0 and not _positive_ending_classification(qa_report):
        blockers.append(
            f"{central_count} live story-debt item(s) overlap the story-bible ending promise without an explicit final-QA classification as resolved or intentional aftermath/sequel residue."
        )

    return blockers


def guard_final_edit_regressions(
    session: Any,
    run: Any,
    chapters: list[Any],
    before: dict[int, tuple[str, int]],
    *,
    pipeline_module: Any,
) -> list[int]:
    """Rollback only catastrophic length regressions introduced by the final line-edit pass."""

    try:
        minimum = int(getattr(run, "min_words_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        minimum = 0
    try:
        maximum = int(getattr(run, "max_words_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        maximum = 0

    rolled_back: list[int] = []
    for chapter in chapters:
        number = int(getattr(chapter, "chapter_number", 0) or 0)
        if number not in before:
            continue
        old_content, old_words = before[number]
        new_words = _word_count(chapter)
        if old_words <= 0 or new_words <= 0 or old_content == str(getattr(chapter, "content", "") or ""):
            continue

        ratio = new_words / old_words
        reasons: list[str] = []
        if ratio < 0.72:
            reasons.append(f"final edit retained only {ratio:.0%} of the prior chapter length")
        elif ratio > 1.35:
            reasons.append(f"final edit expanded to {ratio:.0%} of the prior chapter length")
        if minimum > 0 and old_words >= minimum and new_words < int(minimum * 0.90):
            reasons.append("final edit pushed a previously compliant chapter materially below the configured minimum")
        if maximum > 0 and old_words <= maximum and new_words > int(maximum * 1.20):
            reasons.append("final edit pushed a previously compliant chapter materially above the configured maximum")

        if not reasons:
            continue

        chapter.content = old_content
        chapter.word_count = old_words
        rolled_back.append(number)
        recorder = getattr(pipeline_module, "record_event", None)
        if callable(recorder):
            recorder(
                session,
                run,
                "final_chapter_edit_guard_rollback",
                {
                    "message": f"Rolled back final edit for chapter {number} because it violated line-edit integrity bounds.",
                    "chapter_number": number,
                    "before_word_count": old_words,
                    "rejected_word_count": new_words,
                    "reasons": reasons,
                },
            )

    if rolled_back:
        session.commit()
    return rolled_back


def _wrap_final_editing_pass(final_edit: Callable[..., None], *, pipeline_module: Any) -> Callable[..., None]:
    signature = inspect.signature(final_edit)

    @functools.wraps(final_edit)
    def wrapped(*args: Any, **kwargs: Any) -> None:
        bound = signature.bind_partial(*args, **kwargs)
        chapters = list(bound.arguments.get("chapters") or [])
        before = {
            int(getattr(chapter, "chapter_number", 0) or 0): (
                str(getattr(chapter, "content", "") or ""),
                _word_count(chapter),
            )
            for chapter in chapters
        }
        final_edit(*args, **kwargs)
        try:
            guard_final_edit_regressions(
                bound.arguments["session"],
                bound.arguments["run"],
                chapters,
                before,
                pipeline_module=pipeline_module,
            )
        except Exception:
            # The guard must never turn a successfully edited manuscript into a failed run.
            return

    setattr(wrapped, "_novel_final_edit_guard_wrapped", True)
    return wrapped


def _wrap_readiness_gate(readiness_gate: Callable[..., Any], *, pipeline_module: Any) -> Callable[..., Any]:
    signature = inspect.signature(readiness_gate)

    @functools.wraps(readiness_gate)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        gated = readiness_gate(*args, **kwargs)
        run = bound.arguments.get("run")
        if run is None or not pipeline_module._is_publication_run(run):
            return gated

        blockers = compile_publication_blockers(run, gated)
        if not blockers:
            return gated

        threshold = int(getattr(pipeline_module, "PUBLICATION_READINESS_THRESHOLD", 7) or 7)
        scores = dict(getattr(gated, "publication_readiness_scores", None) or {})
        current_score = int(scores.get("publication_readiness", 10) or 0)
        scores["publication_readiness"] = min(current_score, max(0, threshold - 1))
        warnings = list(getattr(gated, "warnings", None) or [])
        warnings.extend(f"Publication blocker: {item}" for item in blockers)
        dedupe = getattr(pipeline_module, "_dedupe", None)
        if callable(dedupe):
            warnings = dedupe(warnings)

        summary = (
            "Final QA did not pass the publication-readiness guard: "
            + " ".join(blockers)
        )
        updated = gated.model_copy(
            update={
                "warnings": warnings,
                "publication_readiness_scores": scores,
                "publication_readiness_label": "needs editorial revision",
                "publication_readiness_summary": summary,
            }
        )

        recorder = getattr(pipeline_module, "record_event", None)
        session = bound.arguments.get("session")
        if callable(recorder) and session is not None:
            recorder(
                session,
                run,
                "publication_readiness_guard_applied",
                {
                    "message": summary,
                    "blocker_count": len(blockers),
                    "blockers": blockers,
                    "publication_readiness_score": scores["publication_readiness"],
                    "threshold": threshold,
                },
            )
            session.commit()
        return updated

    setattr(wrapped, "_novel_publication_readiness_guard_wrapped", True)
    return wrapped


def install_publication_guard_runtime() -> int:
    """Install deterministic final-edit and publication-readiness safeguards."""

    global _INSTALLED
    if _INSTALLED:
        return 0

    from . import pipeline

    patched = 0
    final_edit = getattr(pipeline, "_run_final_editing_pass", None)
    if final_edit is not None and not getattr(final_edit, "_novel_final_edit_guard_wrapped", False):
        setattr(
            pipeline,
            "_run_final_editing_pass",
            _wrap_final_editing_pass(final_edit, pipeline_module=pipeline),
        )
        patched += 1

    readiness_gate = getattr(pipeline, "_apply_publication_readiness_gate", None)
    if readiness_gate is not None and not getattr(readiness_gate, "_novel_publication_readiness_guard_wrapped", False):
        setattr(
            pipeline,
            "_apply_publication_readiness_gate",
            _wrap_readiness_gate(readiness_gate, pipeline_module=pipeline),
        )
        patched += 1

    _INSTALLED = True
    return patched
