from __future__ import annotations

import functools
import inspect
import json
import os
from typing import Any, Callable

from .chapter_recall import compile_chapter_recall


_INSTALLED = False
_RECALL_BUILDERS = (
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
    value = os.getenv("NOVEL_CHAPTER_RECALL_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _budget_chars() -> int:
    raw = os.getenv("NOVEL_CHAPTER_RECALL_MAX_CHARS", "9000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 9_000
    return min(24_000, max(3_000, parsed))


def _recent_count() -> int:
    raw = os.getenv("NOVEL_CHAPTER_RECALL_RECENT", "2").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 2
    return min(4, max(0, parsed))


def _relevant_count() -> int:
    raw = os.getenv("NOVEL_CHAPTER_RECALL_RELEVANT", "4").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 4
    return min(8, max(0, parsed))


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


def _run_for_bound(bound: inspect.BoundArguments) -> Any:
    run = bound.arguments.get("run")
    if run is not None:
        return run
    chapter = bound.arguments.get("chapter")
    return getattr(chapter, "run", None) if chapter is not None else None


def _chapter_number(bound: inspect.BoundArguments) -> int:
    chapter = bound.arguments.get("chapter")
    if chapter is None:
        return 0
    try:
        return int(getattr(chapter, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _focus(bound: inspect.BoundArguments) -> dict[str, Any]:
    chapter = bound.arguments.get("chapter")
    return {
        "chapter": {
            "title": getattr(chapter, "title", "") if chapter is not None else "",
            "outline_summary": getattr(chapter, "outline_summary", "") if chapter is not None else "",
            "summary": getattr(chapter, "summary", "") if chapter is not None else "",
        },
        "outline": _as_dict(bound.arguments.get("outline_entry")),
        "plan": _as_dict(bound.arguments.get("plan")),
        "developmental_action": _as_dict(bound.arguments.get("developmental_action")),
    }


def _inject_recall(
    messages: list[dict[str, str]],
    *,
    run: Any,
    chapter_number: int,
    focus: Any,
    max_chars: int,
    recent_chapters: int,
    relevant_chapters: int,
) -> list[dict[str, str]]:
    packet = compile_chapter_recall(
        run,
        chapter_number,
        focus=focus,
        max_chars=max_chars,
        recent_chapters=recent_chapters,
        relevant_chapters=relevant_chapters,
    ).payload
    if not packet or not packet.get("chapters"):
        return messages

    block = (
        "Long-range chapter recall (completed-book memory; use only relevant callbacks and do not recap it):\n"
        + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
    markers = (
        "Story arc audit (read-only long-form state",
        "Narrative horizon (causal contract",
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


def _wrap_builder(
    builder: Callable[..., list[dict[str, str]]],
    *,
    max_chars: int,
    recent_chapters: int,
    relevant_chapters: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            return _inject_recall(
                messages,
                run=_run_for_bound(bound),
                chapter_number=_chapter_number(bound),
                focus=_focus(bound),
                max_chars=max_chars,
                recent_chapters=recent_chapters,
                relevant_chapters=relevant_chapters,
            )
        except Exception:
            return messages

    setattr(wrapped, "_novel_recall_wrapped", True)
    return wrapped


def install_recall_runtime() -> int:
    """Install bounded long-range chapter recall into chapter-level model prompts."""

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    patched = 0
    budget = _budget_chars()
    recent = _recent_count()
    relevant = _relevant_count()
    for name in _RECALL_BUILDERS:
        builder = getattr(pipeline, name, None)
        if builder is None or getattr(builder, "_novel_recall_wrapped", False):
            continue
        setattr(
            pipeline,
            name,
            _wrap_builder(
                builder,
                max_chars=budget,
                recent_chapters=recent,
                relevant_chapters=relevant,
            ),
        )
        patched += 1

    _INSTALLED = True
    return patched
