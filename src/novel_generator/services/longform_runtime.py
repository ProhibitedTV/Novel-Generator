from __future__ import annotations

import functools
import inspect
import json
import os
from typing import Any, Callable

from .ending_debt import compile_ending_debt_audit, ending_debt_note
from .manuscript_qa_context import compile_manuscript_qa_capsules
from .quality_trends import compile_quality_trend_audit
from .story_arc_context import compile_story_arc_audit


_INSTALLED = False
_ARC_BUILDERS = (
    "build_chapter_plan_messages",
    "build_chapter_draft_messages",
    "build_chapter_critique_messages",
    "build_chapter_revision_messages",
    "build_chapter_expansion_messages",
    "build_chapter_edit_messages",
    "build_developmental_revision_messages",
    "build_publication_humanization_messages",
    "build_publication_compression_messages",
)


def _enabled() -> bool:
    value = os.getenv("NOVEL_ARC_CONTEXT_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _arc_budget_chars() -> int:
    raw = os.getenv("NOVEL_ARC_CONTEXT_MAX_CHARS", "12000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 12_000
    return min(30_000, max(4_000, parsed))


def _dormant_after_chapters() -> int:
    raw = os.getenv("NOVEL_ARC_DORMANT_AFTER_CHAPTERS", "5").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 5
    return min(16, max(2, parsed))


def _qa_budget_chars() -> int:
    raw = os.getenv("NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS", "70000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 70_000
    return min(160_000, max(30_000, parsed))


def _run_for_bound(bound: inspect.BoundArguments) -> Any:
    run = bound.arguments.get("run")
    if run is not None:
        return run
    chapter = bound.arguments.get("chapter")
    if chapter is not None:
        return getattr(chapter, "run", None)
    chapters = bound.arguments.get("chapters") or []
    if chapters:
        return getattr(chapters[0], "run", None)
    return None


def _chapter_number_for_bound(bound: inspect.BoundArguments) -> int:
    chapter = bound.arguments.get("chapter")
    if chapter is None:
        return 0
    try:
        return int(getattr(chapter, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _inject_arc_audit(
    messages: list[dict[str, str]],
    *,
    run: Any,
    chapter_number: int,
    max_chars: int,
    dormant_after: int,
    label: str = "Story arc audit",
) -> list[dict[str, str]]:
    if run is None or chapter_number <= 0:
        return messages
    audit = compile_story_arc_audit(
        run,
        chapter_number,
        max_chars=max_chars,
        dormant_after=dormant_after,
    ).payload
    if not audit:
        return messages

    block = (
        f"{label} (read-only long-form state; use selectively and do not quote it in prose):\n"
        + json.dumps(audit, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
    markers = (
        "Narrative horizon (causal contract",
        "Current chapter outline:\n",
        "Chapter outline:\n",
        "Deterministic lint findings:\n",
        "Manuscript QA context:\n",
    )
    rewritten: list[dict[str, str]] = []
    injected = False
    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        if not injected and item.get("role") == "user":
            insertion = -1
            for marker in markers:
                insertion = content.find(marker)
                if insertion >= 0:
                    break
            if insertion >= 0:
                item["content"] = content[:insertion] + block + content[insertion:]
            else:
                item["content"] = block + content
            injected = True
        rewritten.append(item)
    return rewritten if injected else messages


def _inject_quality_trend_audit(
    messages: list[dict[str, str]],
    chapters: list[Any],
) -> list[dict[str, str]]:
    audit = compile_quality_trend_audit(chapters).payload
    if not audit:
        return messages
    block = (
        "Whole-book quality trend audit (deterministic signals; inspect sustained drift rather than treating every flag as a mandatory rewrite):\n"
        + json.dumps(audit, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
    rewritten: list[dict[str, str]] = []
    injected = False
    for message in messages:
        item = dict(message)
        if not injected and item.get("role") == "user":
            item["content"] = block + item.get("content", "")
            injected = True
        rewritten.append(item)
    return rewritten if injected else messages


def _inject_ending_debt_audit(
    messages: list[dict[str, str]],
    *,
    run: Any,
    chapters: list[Any],
) -> list[dict[str, str]]:
    audit = compile_ending_debt_audit(run, chapters).payload
    if not audit:
        return messages
    block = (
        "End-of-book story-debt audit (deterministic checkpoint; explicitly decide what is resolved, intentional aftermath/sequel residue, or accidentally abandoned):\n"
        + json.dumps(audit, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
    rewritten: list[dict[str, str]] = []
    injected = False
    for message in messages:
        item = dict(message)
        if not injected and item.get("role") == "user":
            item["content"] = block + item.get("content", "")
            injected = True
        rewritten.append(item)
    return rewritten if injected else messages


def _wrap_arc_builder(
    builder: Callable[..., list[dict[str, str]]],
    *,
    max_chars: int,
    dormant_after: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            return _inject_arc_audit(
                messages,
                run=_run_for_bound(bound),
                chapter_number=_chapter_number_for_bound(bound),
                max_chars=max_chars,
                dormant_after=dormant_after,
            )
        except Exception:
            return messages

    setattr(wrapped, "_novel_arc_wrapped", True)
    return wrapped


def _rewrite_manuscript_qa(
    messages: list[dict[str, str]],
    chapters: Any,
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    packet = compile_manuscript_qa_capsules(chapters or [], max_chars=max_chars)
    compact_json = json.dumps(packet.chapters, ensure_ascii=False, separators=(",", ":"))
    label = "Chapter summaries and QA notes:\n"
    marker = "\n\nReturn a JSON object with exactly these keys:"
    rewritten: list[dict[str, str]] = []
    replaced = False
    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        start = content.find(label)
        if start >= 0:
            data_start = start + len(label)
            end = content.find(marker, data_start)
            if end >= 0:
                item["content"] = content[:data_start] + compact_json + content[end:]
                replaced = True
        rewritten.append(item)
    return rewritten if replaced else messages


def _wrap_manuscript_qa(
    builder: Callable[..., list[dict[str, str]]],
    *,
    qa_max_chars: int,
    arc_max_chars: int,
    dormant_after: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            chapters = list(bound.arguments.get("chapters") or [])
            messages = _rewrite_manuscript_qa(messages, chapters, max_chars=qa_max_chars)
            run = _run_for_bound(bound)
            if run is not None and chapters:
                last_number = max(int(getattr(chapter, "chapter_number", 0) or 0) for chapter in chapters)
                messages = _inject_arc_audit(
                    messages,
                    run=run,
                    chapter_number=last_number + 1,
                    max_chars=arc_max_chars,
                    dormant_after=dormant_after,
                    label="Whole-book unresolved arc audit",
                )
                messages = _inject_ending_debt_audit(messages, run=run, chapters=chapters)
            messages = _inject_quality_trend_audit(messages, chapters)
            return messages
        except Exception:
            return messages

    setattr(wrapped, "_novel_manuscript_qa_wrapped", True)
    return wrapped


def _wrap_manuscript_qa_runner(
    runner: Callable[..., Any],
    *,
    render_report: Callable[[Any], str],
) -> Callable[..., Any]:
    """Persist deterministic ending-debt evidence even when model QA falls back or overlooks it."""

    signature = inspect.signature(runner)

    @functools.wraps(runner)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = runner(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            run = bound.arguments.get("run")
            chapters = list(bound.arguments.get("chapters") or [])
            if run is None:
                return result
            report, _ = result
            audit = compile_ending_debt_audit(run, chapters)
            if not audit.payload:
                return result
            meta = audit.payload.get("_ending_debt_audit") or {}
            note = ending_debt_note(audit)
            ending_notes = list(getattr(report, "ending_coherence_notes", []) or [])
            if note not in ending_notes:
                ending_notes.append(note)
            warnings = list(getattr(report, "warnings", []) or [])
            central_count = int(meta.get("central_candidate_count", 0) or 0)
            if central_count > 0:
                warning = (
                    f"Ending-debt audit found {central_count} unresolved live item(s) with strong lexical overlap to the story-bible ending promise; "
                    "final editorial review must confirm these are intentional residue rather than abandoned central obligations."
                )
                if warning not in warnings:
                    warnings.append(warning)
            updated = report.model_copy(
                update={
                    "ending_coherence_notes": ending_notes,
                    "warnings": warnings,
                }
            )
            return updated, render_report(updated)
        except Exception:
            # This audit is additive safety evidence. It must not turn a successful QA call into a failed run.
            return result

    setattr(wrapped, "_novel_ending_debt_wrapped", True)
    return wrapped


def install_longform_runtime() -> int:
    """Install derived arc/subplot awareness, trend analysis, ending debt, and bounded manuscript QA context."""

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    arc_budget = _arc_budget_chars()
    dormant_after = _dormant_after_chapters()
    patched = 0
    for name in _ARC_BUILDERS:
        builder = getattr(pipeline, name, None)
        if builder is None or getattr(builder, "_novel_arc_wrapped", False):
            continue
        setattr(
            pipeline,
            name,
            _wrap_arc_builder(
                builder,
                max_chars=arc_budget,
                dormant_after=dormant_after,
            ),
        )
        patched += 1

    qa_builder = getattr(pipeline, "build_manuscript_qa_messages", None)
    if qa_builder is not None and not getattr(qa_builder, "_novel_manuscript_qa_wrapped", False):
        setattr(
            pipeline,
            "build_manuscript_qa_messages",
            _wrap_manuscript_qa(
                qa_builder,
                qa_max_chars=_qa_budget_chars(),
                arc_max_chars=arc_budget,
                dormant_after=dormant_after,
            ),
        )
        patched += 1

    qa_runner = getattr(pipeline, "_run_manuscript_qa", None)
    render_report = getattr(pipeline, "render_qa_report_markdown", None)
    if (
        qa_runner is not None
        and callable(render_report)
        and not getattr(qa_runner, "_novel_ending_debt_wrapped", False)
    ):
        setattr(
            pipeline,
            "_run_manuscript_qa",
            _wrap_manuscript_qa_runner(qa_runner, render_report=render_report),
        )
        patched += 1

    _INSTALLED = True
    return patched
