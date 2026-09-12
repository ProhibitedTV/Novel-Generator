from __future__ import annotations

import functools
import inspect
import json
import os
from typing import Any, Callable

from .context_memory import compile_memory_packet


_INSTALLED = False
_TARGET_BUILDERS = (
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
    value = os.getenv("NOVEL_MEMORY_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _budget_chars() -> int:
    raw = os.getenv("NOVEL_MEMORY_MAX_CHARS", "14000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 14_000
    return min(50_000, max(4_000, parsed))


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
    return {
        "chapter": chapter_payload,
        "outline": _as_dict(bound.arguments.get("outline_entry")),
        "plan": _as_dict(bound.arguments.get("plan")),
        "developmental_action": _as_dict(bound.arguments.get("developmental_action")),
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


def _wrap_builder(builder: Callable[..., list[dict[str, str]]], *, budget_chars: int) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            ledger = bound.arguments.get("continuity_ledger")
            if ledger is None:
                return messages
            packet = compile_memory_packet(
                ledger,
                focus=_focus_payload(bound),
                max_chars=budget_chars,
            )
            if not packet.compacted:
                return messages
            return _rewrite_messages(messages, ledger, packet.payload)
        except Exception:
            # Prompt compaction is an optimization, never a reason to fail a generation run.
            return messages

    setattr(wrapped, "_novel_memory_wrapped", True)
    return wrapped


def install_context_compiler() -> int:
    """Install bounded continuity-memory views into chapter-level pipeline prompt builders.

    Returns the number of builders patched. Installation is process-wide and idempotent.
    """

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    budget = _budget_chars()
    patched = 0
    for name in _TARGET_BUILDERS:
        builder = getattr(pipeline, name, None)
        if builder is None or getattr(builder, "_novel_memory_wrapped", False):
            continue
        setattr(pipeline, name, _wrap_builder(builder, budget_chars=budget))
        patched += 1

    _INSTALLED = True
    return patched
