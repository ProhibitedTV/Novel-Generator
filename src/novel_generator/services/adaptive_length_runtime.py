from __future__ import annotations

import functools
import inspect
import os
from typing import Any, Callable

from .narrative_horizon import _word_budget


_INSTALLED = False


def _enabled() -> bool:
    value = os.getenv("NOVEL_ADAPTIVE_LENGTH_ENFORCEMENT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _max_extra_passes() -> int:
    raw = os.getenv("NOVEL_ADAPTIVE_LENGTH_MAX_EXTRA_PASSES", "1").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 1
    return min(2, max(0, parsed))


def _word_count(chapter: Any) -> int:
    try:
        stored = int(getattr(chapter, "word_count", 0) or 0)
    except (TypeError, ValueError):
        stored = 0
    if stored > 0:
        return stored
    return len(str(getattr(chapter, "content", "") or "").split())


def adaptive_target_words(run: Any, chapter_number: int) -> int:
    """Return the chapter target required to keep the whole manuscript on its configured pace."""

    try:
        total = max(1, int(getattr(run, "requested_chapters", 0) or 0), chapter_number)
        configured_min = int(getattr(run, "min_words_per_chapter", 0) or 0)
        configured_max = int(getattr(run, "max_words_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        return 0
    budget = _word_budget(run, chapter_number, total)
    try:
        adaptive = int(budget.get("adaptive_target_this_chapter", 0) or 0)
    except (TypeError, ValueError):
        adaptive = 0
    if adaptive <= 0:
        return max(0, configured_min)
    if configured_max > 0:
        adaptive = min(adaptive, configured_max)
    return max(configured_min, adaptive)


def _append_target_instruction(messages: list[dict[str, str]], *, target_words: int, max_words: int) -> list[dict[str, str]]:
    instruction = (
        "\n\nWhole-book adaptive length requirement:\n"
        f"- expand this chapter toward at least {target_words} words because the remaining manuscript budget requires it\n"
        f"- do not exceed the configured chapter maximum of {max_words} words\n"
        "- add dramatized action, dialogue, sensory specificity, reaction, complication, and consequence; do not pad with recap, repeated explanation, or redundant internal monologue\n"
        "- preserve all existing events and prose strengths while making the chapter feel intentionally full rather than mechanically stretched"
    )
    rewritten = [dict(message) for message in messages]
    for index in range(len(rewritten) - 1, -1, -1):
        if rewritten[index].get("role") == "user":
            rewritten[index]["content"] = rewritten[index].get("content", "") + instruction
            return rewritten
    return messages


def _wrap_expander(expander: Callable[..., None], *, max_extra_passes: int) -> Callable[..., None]:
    signature = inspect.signature(expander)

    @functools.wraps(expander)
    def wrapped(*args: Any, **kwargs: Any) -> None:
        # Keep the existing static-minimum expansion behavior first. The supplemental pass only
        # activates if the chapter clears that floor but still leaves the whole novel behind pace.
        expander(*args, **kwargs)
        if max_extra_passes <= 0:
            return

        try:
            bound = signature.bind_partial(*args, **kwargs)
            session = bound.arguments["session"]
            run = bound.arguments["run"]
            chapter = bound.arguments["chapter"]
            outline_entry = bound.arguments["outline_entry"]
            story_bible = bound.arguments["story_bible"]
            ledger = bound.arguments["ledger"]
            plan = bound.arguments["plan"]
            client = bound.arguments["client"]
            reason = str(bound.arguments.get("reason") or "adaptive_length")
        except Exception:
            return

        if getattr(chapter, "summary", None):
            return
        chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
        target_words = adaptive_target_words(run, chapter_number)
        configured_min = int(getattr(run, "min_words_per_chapter", 0) or 0)
        configured_max = int(getattr(run, "max_words_per_chapter", 0) or 0)
        if target_words <= configured_min or target_words <= 0 or configured_max <= 0:
            return

        # Import here to avoid a module cycle: this runtime is installed onto pipeline at worker startup.
        from . import pipeline

        for pass_number in range(1, max_extra_passes + 1):
            current_words = _word_count(chapter)
            chapter.word_count = current_words
            if current_words >= target_words:
                break
            pipeline._ensure_not_canceled(session, run)
            provider_name, model_name = pipeline._resolve_stage_route(client, run, "chapter_revision")
            run.current_step = "chapter_adaptive_expansion"
            run.current_chapter = chapter_number
            pipeline.record_event(
                session,
                run,
                "chapter_adaptive_expansion_started",
                {
                    "message": f"Expanding chapter {chapter_number} toward the live whole-book word target.",
                    "chapter_number": chapter_number,
                    "before_word_count": current_words,
                    "adaptive_target_words": target_words,
                    "configured_min_words": configured_min,
                    "configured_max_words": configured_max,
                    "pass_number": pass_number,
                    "reason": reason,
                    "provider_name": provider_name,
                    "model_name": model_name,
                },
            )
            session.commit()

            try:
                messages = pipeline.build_chapter_expansion_messages(
                    run.project,
                    run,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    plan,
                )
                messages = _append_target_instruction(
                    messages,
                    target_words=target_words,
                    max_words=configured_max,
                )
                expanded_content = pipeline.sanitize_chapter_content(
                    pipeline._supervised_provider_chat(
                        session,
                        run,
                        client,
                        provider_name,
                        model_name,
                        messages,
                        stage="chapter_expansion",
                        chapter_number=chapter_number,
                        metadata={
                            "label": f"chapter {chapter_number} adaptive expansion",
                            "reason": reason,
                            "pass_number": pass_number,
                            "before_word_count": current_words,
                            "adaptive_target_words": target_words,
                        },
                    )
                )
                expanded_words = len(expanded_content.split())
                if not expanded_content.strip():
                    raise RuntimeError(f"Chapter {chapter_number} adaptive expansion was empty.")
                if expanded_words < current_words:
                    raise RuntimeError(
                        f"Chapter {chapter_number} adaptive expansion shortened the chapter from "
                        f"{current_words} to {expanded_words} words."
                    )
                chapter.content = expanded_content
                chapter.word_count = expanded_words
                pipeline.record_event(
                    session,
                    run,
                    "chapter_adaptive_expansion_completed",
                    {
                        "message": f"Chapter {chapter_number} adaptive expansion saved.",
                        "chapter_number": chapter_number,
                        "before_word_count": current_words,
                        "after_word_count": expanded_words,
                        "adaptive_target_words": target_words,
                        "pass_number": pass_number,
                        "reason": reason,
                    },
                )
                session.commit()
            except Exception as exc:
                chapter.word_count = _word_count(chapter)
                pipeline.record_event(
                    session,
                    run,
                    "chapter_adaptive_expansion_fallback",
                    {
                        "message": f"Chapter {chapter_number} adaptive expansion failed; keeping existing prose.",
                        "chapter_number": chapter_number,
                        "word_count": chapter.word_count,
                        "adaptive_target_words": target_words,
                        "pass_number": pass_number,
                        "reason": reason,
                        "error": str(exc),
                    },
                )
                session.commit()
                break

        if _word_count(chapter) < target_words:
            pipeline.record_event(
                session,
                run,
                "chapter_adaptive_expansion_shortfall",
                {
                    "message": f"Chapter {chapter_number} remains below its live whole-book word target.",
                    "chapter_number": chapter_number,
                    "word_count": _word_count(chapter),
                    "adaptive_target_words": target_words,
                    "reason": reason,
                },
            )
            session.commit()

    setattr(wrapped, "_novel_adaptive_length_wrapped", True)
    return wrapped


def install_adaptive_length_runtime() -> int:
    """Add a bounded expansion pass when the live whole-book target exceeds the static chapter minimum."""

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    expander = getattr(pipeline, "_expand_chapter_to_minimum", None)
    if expander is None or getattr(expander, "_novel_adaptive_length_wrapped", False):
        _INSTALLED = True
        return 0

    setattr(pipeline, "_expand_chapter_to_minimum", _wrap_expander(expander, max_extra_passes=_max_extra_passes()))
    _INSTALLED = True
    return 1
