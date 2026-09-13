from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NarrativeHorizon:
    payload: dict[str, Any]


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


def _clip(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _outline_rows(run: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in getattr(run, "outline", None) or []:
        item = _as_dict(raw)
        try:
            number = int(item.get("chapter_number", 0))
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        rows.append({**item, "chapter_number": number})
    rows.sort(key=lambda item: item["chapter_number"])
    return rows


def _chapters(run: Any) -> list[Any]:
    chapters = list(getattr(run, "chapters", None) or [])
    chapters.sort(key=lambda item: int(getattr(item, "chapter_number", 0) or 0))
    return chapters


def _chapter_word_count(chapter: Any) -> int:
    try:
        stored = int(getattr(chapter, "word_count", 0) or 0)
    except (TypeError, ValueError):
        stored = 0
    if stored > 0:
        return stored
    content = str(getattr(chapter, "content", "") or "").strip()
    return len(content.split()) if content else 0


def _word_budget(run: Any, chapter_number: int, total_chapters: int) -> dict[str, Any]:
    try:
        target = int(getattr(run, "target_word_count", 0) or 0)
        configured_min = int(getattr(run, "min_words_per_chapter", 0) or 0)
        configured_max = int(getattr(run, "max_words_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        return {}
    if target <= 0 or total_chapters <= 0:
        return {}

    if configured_min <= 0:
        configured_min = max(1, math.floor(target / total_chapters * 0.8))
    if configured_max < configured_min:
        configured_max = max(configured_min, math.ceil(target / total_chapters * 1.2))

    completed_words = sum(
        _chapter_word_count(chapter)
        for chapter in _chapters(run)
        if int(getattr(chapter, "chapter_number", 0) or 0) < chapter_number
    )
    remaining_chapters = max(1, total_chapters - chapter_number + 1)
    remaining_words = max(0, target - completed_words)
    required_average = math.ceil(remaining_words / remaining_chapters) if remaining_words else configured_min
    adaptive_target = min(configured_max, max(configured_min, required_average))

    expected_before_current = target * max(0, chapter_number - 1) / total_chapters
    pace_delta = completed_words - expected_before_current
    tolerance = max(250, target / total_chapters * 0.15)
    if pace_delta < -tolerance:
        pace_status = "behind"
    elif pace_delta > tolerance:
        pace_status = "ahead"
    else:
        pace_status = "on_pace"

    maximum_reachable = completed_words + configured_max * remaining_chapters
    minimum_reachable = completed_words + configured_min * remaining_chapters
    return {
        "novel_target_words": target,
        "completed_words_before_this_chapter": completed_words,
        "remaining_words_including_this_chapter": remaining_words,
        "remaining_chapters_including_this_chapter": remaining_chapters,
        "configured_chapter_range": [configured_min, configured_max],
        "required_average_from_here": required_average,
        "adaptive_target_this_chapter": adaptive_target,
        "pace_status": pace_status,
        "pace_delta_words": round(pace_delta),
        "target_reachable_with_configured_max": maximum_reachable >= target,
        "would_overshoot_if_every_remaining_chapter_hit_min": minimum_reachable > target,
    }


def _compact_outline(item: dict[str, Any]) -> dict[str, Any]:
    hook = _as_dict(item.get("concrete_ending_hook"))
    return {
        "chapter_number": item.get("chapter_number"),
        "act": item.get("act", ""),
        "title": item.get("title", ""),
        "objective": item.get("objective", ""),
        "conflict_turn": item.get("conflict_turn", ""),
        "character_turn": item.get("character_turn", ""),
        "reveal": item.get("reveal", ""),
        "ending_state": item.get("ending_state", ""),
        "outcome_type": item.get("outcome_type", ""),
        "primary_obstacle": item.get("primary_obstacle", ""),
        "cost_if_success": item.get("cost_if_success", ""),
        "chapter_mode": item.get("chapter_mode", ""),
        "ending_hook": {
            "trigger": hook.get("trigger", ""),
            "visible_object_or_actor": hook.get("visible_object_or_actor", ""),
            "next_problem": hook.get("next_problem", ""),
        },
    }


def _previous_chapter_state(run: Any, chapter_number: int) -> dict[str, Any]:
    prior = [
        chapter
        for chapter in _chapters(run)
        if int(getattr(chapter, "chapter_number", 0) or 0) < chapter_number
        and (getattr(chapter, "summary", None) or getattr(chapter, "continuity_update", None))
    ]
    if not prior:
        return {}

    chapter = prior[-1]
    continuity = _as_dict(getattr(chapter, "continuity_update", None))
    turn = _as_dict(continuity.get("story_turn"))
    return {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": getattr(chapter, "title", "") or "",
        "summary": getattr(chapter, "summary", "") or "",
        "irreversible_change": turn.get("irreversible_change", ""),
        "protagonist_choice": turn.get("protagonist_choice", ""),
        "permanent_consequence": turn.get("permanent_consequence", ""),
        "state_after": turn.get("state_after", ""),
        "chapter_outcome": continuity.get("chapter_outcome", ""),
    }


def _recent_patterns(run: Any, chapter_number: int, *, window: int = 4) -> list[dict[str, Any]]:
    outline_by_number = {int(item["chapter_number"]): item for item in _outline_rows(run)}
    prior = [
        chapter
        for chapter in _chapters(run)
        if int(getattr(chapter, "chapter_number", 0) or 0) < chapter_number
        and (getattr(chapter, "content", None) or getattr(chapter, "summary", None))
    ][-window:]

    rows: list[dict[str, Any]] = []
    for chapter in prior:
        number = int(getattr(chapter, "chapter_number", 0) or 0)
        outline = outline_by_number.get(number, {})
        plan = _safe_json_dict(getattr(chapter, "plan", None))
        hook = _as_dict(outline.get("concrete_ending_hook"))
        rows.append(
            {
                "chapter_number": number,
                "chapter_mode": plan.get("chapter_mode") or outline.get("chapter_mode", ""),
                "primary_obstacle": outline.get("primary_obstacle", ""),
                "conflict_turn": plan.get("conflict_turn") or outline.get("conflict_turn", ""),
                "emotional_anchor": plan.get("emotional_anchor", ""),
                "side_character_move": plan.get("independent_side_character_move")
                or outline.get("independent_side_character_move", ""),
                "ending_hook_trigger": hook.get("trigger", ""),
            }
        )
    return rows


def _selected_mapping(value: Any, limit: int) -> dict[str, str]:
    mapping = value if isinstance(value, dict) else {}
    return {
        str(key): _clip(item)
        for key, item in list(mapping.items())[-max(0, limit):]
        if str(key).strip() and str(item).strip()
    }


def _selected_list(value: Any, limit: int) -> list[str]:
    items = list(value) if isinstance(value, list) else []
    return [_clip(item) for item in items[-max(0, limit):] if str(item).strip()]


def _ending_convergence(run: Any, chapter_number: int, total: int, progress: float) -> dict[str, Any]:
    """Create late-book closure pressure without inventing new plot state."""

    if progress < 0.70 or total <= 0:
        return {}

    ledger = _as_dict(getattr(run, "continuity_ledger", None))
    bible = _as_dict(getattr(run, "story_bible", None))
    promises = ledger.get("open_promises_by_name") if isinstance(ledger.get("open_promises_by_name"), dict) else {}
    threads = ledger.get("open_threads") if isinstance(ledger.get("open_threads"), list) else []
    emotions = ledger.get("emotional_open_loops") if isinstance(ledger.get("emotional_open_loops"), dict) else {}
    trust = ledger.get("trust_fractures") if isinstance(ledger.get("trust_fractures"), dict) else {}
    remaining = max(1, total - chapter_number + 1)

    if progress >= 0.90 or remaining <= 2:
        phase = "resolution_priority"
        new_major_threads_allowed = 0
    elif progress >= 0.80:
        phase = "convergence"
        new_major_threads_allowed = 0
    else:
        phase = "prepare_convergence"
        new_major_threads_allowed = 1

    rules = [
        "Treat unresolved promises and threads as a finite closure budget: advance, transform, or resolve existing lanes before inventing new major ones.",
        "Do not add a new major faction, mystery, system, conspiracy, villain, quest, or relationship crisis unless it is already seeded or can pay off inside the remaining chapters.",
        "Use late revelations to reinterpret established material rather than replace the book's central conflict with a new one.",
        "Protect one primary climax and one primary ending; do not manufacture serial fake endings or a second unrelated climax.",
        "Preserve enough aftermath after the decisive turn to show human, relational, civic, and world-state consequences.",
    ]
    if phase == "resolution_priority":
        rules.extend(
            [
                "No new major unresolved thread should survive this chapter unless the existing outline explicitly requires it for the ending.",
                "Prefer resolving or irreversibly transforming at least one open promise, trust fracture, or emotional loop in each remaining chapter.",
                "A sequel hook may remain only after the current book's central emotional and external promises receive closure.",
            ]
        )

    return {
        "phase": phase,
        "remaining_chapters_including_this_chapter": remaining,
        "new_major_threads_allowed": new_major_threads_allowed,
        "ending_promise": _clip(bible.get("ending_promise", ""), 700),
        "open_promise_count": len(promises),
        "open_thread_count": len(threads),
        "emotional_open_loop_count": len(emotions),
        "trust_fracture_count": len(trust),
        "priority_open_promises": _selected_mapping(promises, 6),
        "priority_open_threads": _selected_list(threads, 6),
        "priority_emotional_loops": _selected_mapping(emotions, 4),
        "priority_trust_fractures": _selected_mapping(trust, 4),
        "rules": rules,
    }


def compile_narrative_horizon(
    run: Any,
    chapter_number: int,
    *,
    lookahead: int = 3,
) -> NarrativeHorizon:
    """Compile a compact causal, pacing, and convergence contract for one chapter.

    The packet is derived only from persisted run state. It creates no new canon. It gives a local
    model the previous permanent consequence, near-future commitments, recent pattern history,
    remaining novel word budget, and late-book closure pressure without asking the model to infer
    those constraints from an entire manuscript.
    """

    outline = _outline_rows(run)
    current_index = next(
        (index for index, item in enumerate(outline) if item["chapter_number"] == chapter_number),
        None,
    )
    if current_index is None:
        return NarrativeHorizon(payload={})

    current = outline[current_index]
    previous_outline = outline[current_index - 1] if current_index > 0 else None
    upcoming = outline[current_index + 1 : current_index + 1 + max(0, lookahead)]
    total = max(len(outline), int(getattr(run, "requested_chapters", 0) or 0), chapter_number)
    progress = chapter_number / total if total else 0.0

    causal_inheritance = _previous_chapter_state(run, chapter_number)
    if previous_outline:
        causal_inheritance = {
            "previous_outline_ending_state": previous_outline.get("ending_state", ""),
            "previous_outline_cost": previous_outline.get("cost_if_success", ""),
            "previous_outline_hook": _as_dict(previous_outline.get("concrete_ending_hook")),
            **causal_inheritance,
        }

    payload = {
        "_narrative_horizon": {
            "chapter_number": chapter_number,
            "total_chapters": total,
            "book_progress_percent": round(progress * 100, 1),
            "current_act": current.get("act", ""),
            "lookahead_chapters": len(upcoming),
        },
        "word_budget": _word_budget(run, chapter_number, total),
        "ending_convergence": _ending_convergence(run, chapter_number, total, progress),
        "causal_inheritance": causal_inheritance,
        "upcoming_commitments": [_compact_outline(item) for item in upcoming],
        "recent_pattern_history": _recent_patterns(run, chapter_number),
        "causal_rules": [
            "Open from the state produced by the previous completed chapter; do not reset relationships, knowledge, injuries, leverage, or public consequences.",
            "The current chapter must create at least one concrete precondition used by the next planned chapter.",
            "Do not resolve a later reveal, reversal, or ending hook early merely because it appears in lookahead context.",
            "Avoid reusing the same obstacle shape, dramatic mode, emotional beat, side-character function, or ending-hook mechanism visible in recent_pattern_history unless repetition is itself the point.",
            "Treat upcoming_commitments as promises to prepare, not scenes to steal from future chapters.",
            "When word_budget is present, aim near adaptive_target_this_chapter while staying inside configured_chapter_range; do not intentionally hug the minimum when the novel is behind pace.",
            "When ending_convergence is present, obey its closure phase and spend existing unresolved story debt before creating new major story debt.",
        ],
    }
    return NarrativeHorizon(payload=payload)
