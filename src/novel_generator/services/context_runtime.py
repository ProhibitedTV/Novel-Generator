from __future__ import annotations

import functools
import inspect
import json
import os
from typing import Any, Callable

from .context_memory import compile_memory_packet
from .manuscript_context import compile_manuscript_capsules
from .narrative_horizon import compile_narrative_horizon


_INSTALLED = False
_TARGET_BUILDERS = (
    "build_chapter_plan_messages",
    "build_chapter_draft_messages",
    "build_chapter_critique_messages",
    "build_chapter_revision_messages",
    "build_chapter_expansion_messages",
    "build_chapter_edit_messages",
    "build_developmental_rewrite_messages",
    "build_developmental_revision_messages",
    "build_publication_humanization_messages",
    "build_publication_compression_messages",
)
_HORIZON_BUILDERS = {
    "build_chapter_plan_messages",
    "build_chapter_draft_messages",
    "build_chapter_critique_messages",
    "build_chapter_revision_messages",
    "build_chapter_expansion_messages",
}


def _enabled() -> bool:
    value = os.getenv("NOVEL_MEMORY_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _budget_chars() -> int:
    raw = os.getenv("NOVEL_MEMORY_MAX_CHARS", "14000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 14_000
    return min(50_000, max(4_000, parsed))


def _manuscript_budget_chars() -> int:
    raw = os.getenv("NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS", "70000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 70_000
    return min(160_000, max(30_000, parsed))


def _horizon_lookahead() -> int:
    raw = os.getenv("NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS", "3").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 3
    return min(6, max(1, parsed))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _focus_payload(bound: inspect.BoundArguments) -> dict[str, Any]:
    chapter = bound.arguments.get("chapter")
    chapter_payload = {}
    if chapter is not None:
        chapter_payload = {
            "chapter_number": getattr(chapter, "chapter_number", None),
            "title": getattr(chapter, "title", ""),
            "outline_summary": getattr(chapter, "outline_summary", ""),
            "summary": getattr(chapter, "summary", ""),
        }
    chapters = bound.arguments.get("chapters") or []
    manuscript_focus = [
        {
            "chapter_number": getattr(item, "chapter_number", None),
            "title": getattr(item, "title", ""),
            "outline_summary": getattr(item, "outline_summary", ""),
            "summary": getattr(item, "summary", ""),
        }
        for item in chapters
    ]
    return {
        "chapter": chapter_payload,
        "chapters": manuscript_focus,
        "outline": _as_dict(bound.arguments.get("outline_entry")),
        "plan": _as_dict(bound.arguments.get("plan")),
        "developmental_action": _as_dict(bound.arguments.get("developmental_action")),
        "qa_report": _as_dict(bound.arguments.get("qa_report")),
        "prior_context": bound.arguments.get("prior_context", ""),
    }


def _rewrite_messages(messages: list[dict[str, str]], ledger: Any, packet: dict[str, Any]) -> list[dict[str, str]]:
    ledger_payload = _as_dict(ledger)
    full_json = json.dumps(ledger_payload, indent=2)
    compact_json = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    rewritten: list[dict[str, str]] = []
    replaced = False
    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        if full_json in content:
            item["content"] = content.replace(full_json, compact_json)
            replaced = True
        rewritten.append(item)

    if not replaced:
        # Builders in this module currently serialize the ledger with ``json.dumps(..., indent=2)``.
        # If that changes later, preserve the original prompt rather than risking a malformed rewrite.
        return messages
    return rewritten


def _rewrite_manuscript_chapters(
    messages: list[dict[str, str]],
    chapters: Any,
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    packet = compile_manuscript_capsules(chapters or [], max_chars=max_chars)
    compact_json = json.dumps(packet.chapters, ensure_ascii=False, separators=(",", ":"))
    label = "Full manuscript chapters:\n"
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


def _run_for_bound(bound: inspect.BoundArguments) -> Any:
    run = bound.arguments.get("run")
    if run is not None:
        return run
    chapter = bound.arguments.get("chapter")
    return getattr(chapter, "run", None) if chapter is not None else None


def _chapter_number_for_bound(bound: inspect.BoundArguments) -> int:
    chapter = bound.arguments.get("chapter")
    if chapter is None:
        return 0
    try:
        return int(getattr(chapter, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _inject_narrative_horizon(
    messages: list[dict[str, str]],
    *,
    run: Any,
    chapter_number: int,
    lookahead: int,
) -> list[dict[str, str]]:
    if run is None or chapter_number <= 0:
        return messages
    horizon = compile_narrative_horizon(run, chapter_number, lookahead=lookahead).payload
    if not horizon:
        return messages

    block = (
        "Narrative horizon (causal contract; use it to preserve long-range structure and do not quote it in prose):\n"
        + json.dumps(horizon, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
    preferred_markers = (
        "Current chapter outline:\n",
        "Chapter outline:\n",
        "Deterministic lint findings:\n",
    )
    rewritten: list[dict[str, str]] = []
    injected = False
    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        if not injected and item.get("role") == "user":
            insertion = -1
            for marker in preferred_markers:
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


def _wrap_builder(
    builder: Callable[..., list[dict[str, str]]],
    *,
    budget_chars: int,
    horizon_lookahead: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)
    builder_name = getattr(builder, "__name__", "")

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            ledger = bound.arguments.get("continuity_ledger")
            if ledger is not None:
                packet = compile_memory_packet(
                    ledger,
                    focus=_focus_payload(bound),
                    max_chars=budget_chars,
                )
                if packet.compacted:
                    messages = _rewrite_messages(messages, ledger, packet.payload)

            if builder_name in _HORIZON_BUILDERS:
                messages = _inject_narrative_horizon(
                    messages,
                    run=_run_for_bound(bound),
                    chapter_number=_chapter_number_for_bound(bound),
                    lookahead=horizon_lookahead,
                )
            return messages
        except Exception:
            # Context shaping is an optimization, never a reason to fail a generation run.
            return messages

    setattr(wrapped, "_novel_memory_wrapped", True)
    return wrapped


def _wrap_developmental_rewrite(
    builder: Callable[..., list[dict[str, str]]],
    *,
    max_chars: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            chapters = bound.arguments.get("chapters") or []
            return _rewrite_manuscript_chapters(messages, chapters, max_chars=max_chars)
        except Exception:
            return messages

    setattr(wrapped, "_manuscript_capsules_wrapped", True)
    return wrapped


def install_context_compiler() -> int:
    """Install bounded continuity, narrative-horizon, and whole-manuscript prompt views.

    Returns the number of prompt transformations installed. Installation is process-wide and
    idempotent. The durable continuity ledger, outline, and saved chapter prose remain unchanged.
    """

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    budget = _budget_chars()
    horizon_lookahead = _horizon_lookahead()
    patched = 0
    for name in _TARGET_BUILDERS:
        builder = getattr(pipeline, name, None)
        if builder is None or getattr(builder, "_novel_memory_wrapped", False):
            continue
        setattr(
            pipeline,
            name,
            _wrap_builder(
                builder,
                budget_chars=budget,
                horizon_lookahead=horizon_lookahead,
            ),
        )
        patched += 1

    developmental_builder = getattr(pipeline, "build_developmental_rewrite_messages", None)
    if developmental_builder is not None and not getattr(developmental_builder, "_manuscript_capsules_wrapped", False):
        setattr(
            pipeline,
            "build_developmental_rewrite_messages",
            _wrap_developmental_rewrite(
                developmental_builder,
                max_chars=_manuscript_budget_chars(),
            ),
        )
        patched += 1

    _INSTALLED = True
    return patched
