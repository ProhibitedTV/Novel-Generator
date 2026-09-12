from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ManuscriptQaCapsules:
    chapters: list[dict[str, Any]]
    output_chars: int
    mode: str


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


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _clip_mapping(value: Any, limit: int, item_limit: int) -> dict[str, Any]:
    mapping = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    for key, item in list(mapping.items())[-limit:]:
        if isinstance(item, list):
            result[str(key)] = [_clip(entry, item_limit) for entry in item[-3:]]
        else:
            result[str(key)] = _clip(item, item_limit)
    return result


def _qa_notes(value: Any, *, compact: bool) -> dict[str, Any]:
    qa = _as_dict(value)
    score_keys = (
        "ending_hook_type",
        "forward_motion_score",
        "ending_concreteness_score",
        "scene_turn_resolution_score",
        "cost_consequence_realism_score",
        "side_character_independence_score",
        "proper_noun_continuity_score",
        "repetition_risk_score",
        "emotional_depth_score",
        "ideology_clarity_score",
        "civilian_texture_score",
        "genre_contract_score",
        "style_alignment_score",
        "voice_distinctness_score",
        "technical_escalation_fatigue_score",
        "irreversibility_score",
        "choice_clarity_score",
        "cuttable_chapter_risk_score",
    )
    payload = {key: qa.get(key) for key in score_keys if qa.get(key) not in (None, "", [])}
    max_items = 2 if compact else 5
    for key in ("warnings", "blocking_issues", "soft_warnings", "focus"):
        items = qa.get(key)
        if isinstance(items, list) and items:
            payload[key] = [_clip(item, 260 if compact else 420) for item in items[:max_items]]
    if qa.get("revision_required") is not None:
        payload["revision_required"] = bool(qa.get("revision_required"))
    return payload


def _continuity_signals(value: Any, *, compact: bool) -> dict[str, Any]:
    continuity = _as_dict(value)
    turn = _as_dict(continuity.get("story_turn"))
    text_limit = 220 if compact else 420
    map_limit = 3 if compact else 6
    transition_limit = 2 if compact else 5
    signals: dict[str, Any] = {}

    if continuity.get("chapter_outcome"):
        signals["chapter_outcome"] = _clip(continuity.get("chapter_outcome"), text_limit)
    if turn:
        signals["story_turn"] = {
            key: _clip(turn.get(key), text_limit)
            for key in (
                "irreversible_change",
                "protagonist_choice",
                "permanent_consequence",
                "why_this_chapter_cannot_be_cut",
                "state_after",
            )
            if turn.get(key)
        }

    for key in ("entity_state_changes", "ideology_shift_notes", "memory_damage", "trust_fractures", "emotional_open_loops", "side_character_decisions"):
        clipped = _clip_mapping(continuity.get(key), map_limit, text_limit)
        if clipped:
            signals[key] = clipped

    genre_state = _clip_mapping(continuity.get("genre_state"), map_limit, text_limit)
    if genre_state:
        signals["genre_state"] = genre_state

    transitions = continuity.get("system_state_transitions")
    if isinstance(transitions, list) and transitions:
        signals["system_state_transitions"] = [
            {
                key: (_clip(item.get(key), text_limit) if key != "chapter_number" else item.get(key))
                for key in ("system_name", "previous_state", "new_state", "cause", "chapter_number")
                if _as_dict(item).get(key) not in (None, "")
            }
            for item in [_as_dict(entry) for entry in transitions[-transition_limit:]]
        ]

    new_entities = continuity.get("new_entities_introduced")
    if isinstance(new_entities, list) and new_entities:
        signals["new_entities_introduced"] = [
            {
                "name": _clip(_as_dict(item).get("name", ""), 100),
                "kind": _clip(_as_dict(item).get("kind", ""), 80),
                "role": _clip(_as_dict(item).get("role", ""), text_limit),
            }
            for item in new_entities[-transition_limit:]
        ]
    return signals


def _capsule(chapter: Any, *, mode: str) -> dict[str, Any]:
    compact = mode != "detailed"
    minimal = mode == "minimal"
    payload: dict[str, Any] = {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": _clip(getattr(chapter, "title", ""), 160),
        "summary": _clip(getattr(chapter, "summary", ""), 360 if minimal else (700 if compact else 1200)),
        "word_count": int(getattr(chapter, "word_count", 0) or 0),
    }
    continuity = _continuity_signals(getattr(chapter, "continuity_update", None), compact=compact)
    if continuity:
        payload["continuity_signals"] = continuity
    if not minimal:
        qa = _qa_notes(getattr(chapter, "qa_notes", None), compact=compact)
        if qa:
            payload["qa_notes"] = qa
        outline_summary = _clip(getattr(chapter, "outline_summary", ""), 420 if compact else 800)
        if outline_summary:
            payload["outline_summary"] = outline_summary
    return payload


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compile_manuscript_qa_capsules(
    chapters: Iterable[Any],
    *,
    max_chars: int = 70_000,
) -> ManuscriptQaCapsules:
    """Bound the whole-book QA view while retaining one structural record per chapter."""

    rows = list(chapters)
    budget = max(30_000, int(max_chars))
    if not rows:
        return ManuscriptQaCapsules(chapters=[], output_chars=2, mode="detailed")

    for mode in ("detailed", "compact", "minimal"):
        payload = [_capsule(chapter, mode=mode) for chapter in rows]
        size = _json_chars(payload)
        if size <= budget:
            return ManuscriptQaCapsules(chapters=payload, output_chars=size, mode=mode)

    # Final fallback still preserves chapter number/title/summary/word count for every chapter.
    per_chapter = max(80, min(220, budget // max(1, len(rows)) - 120))
    payload = [
        {
            "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
            "title": _clip(getattr(chapter, "title", ""), 100),
            "summary": _clip(getattr(chapter, "summary", ""), per_chapter),
            "word_count": int(getattr(chapter, "word_count", 0) or 0),
        }
        for chapter in rows
    ]
    return ManuscriptQaCapsules(chapters=payload, output_chars=_json_chars(payload), mode="minimal")
