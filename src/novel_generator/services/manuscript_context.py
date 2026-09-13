from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ManuscriptCapsules:
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


def _text(value: Any, limit: int) -> str:
    rendered = " ".join(str(value or "").split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(1, limit - 1)].rstrip() + "…"


def _excerpt(value: Any, limit: int, *, tail: bool = False) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        return ""
    if len(rendered) <= limit:
        return rendered
    if tail:
        return "…" + rendered[-max(1, limit - 1):].lstrip()
    return rendered[: max(1, limit - 1)].rstrip() + "…"


def _qa_context(value: Any, *, compact: bool) -> dict[str, Any]:
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
    )
    payload = {key: qa.get(key) for key in score_keys if qa.get(key) not in (None, "", [])}
    warnings = list(qa.get("warnings") or [])
    focus = list(qa.get("focus") or [])
    if warnings:
        payload["warnings"] = [_text(item, 320) for item in warnings[: (2 if compact else 5)]]
    if focus:
        payload["focus"] = [_text(item, 320) for item in focus[: (2 if compact else 5)]]
    if qa.get("revision_required") is not None:
        payload["revision_required"] = bool(qa.get("revision_required"))
    return payload


def _story_turn(chapter: Any, *, compact: bool) -> dict[str, Any]:
    continuity = _as_dict(getattr(chapter, "continuity_update", None))
    turn = _as_dict(continuity.get("story_turn"))
    if not turn:
        return {}
    limit = 280 if compact else 520
    return {str(key): _text(value, limit) if isinstance(value, str) else value for key, value in turn.items()}


def _capsule(chapter: Any, *, mode: str) -> dict[str, Any]:
    compact = mode != "detailed"
    minimal = mode == "minimal"
    content = str(getattr(chapter, "content", "") or "")
    payload: dict[str, Any] = {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": _text(getattr(chapter, "title", ""), 180),
        "outline_summary": _text(getattr(chapter, "outline_summary", ""), 320 if minimal else (600 if compact else 1200)),
        "summary": _text(getattr(chapter, "summary", ""), 420 if minimal else (750 if compact else 1400)),
        "word_count": int(getattr(chapter, "word_count", 0) or 0),
        "story_turn": _story_turn(chapter, compact=compact),
    }
    if not minimal:
        qa = _qa_context(getattr(chapter, "qa_notes", None), compact=compact)
        if qa:
            payload["qa_notes"] = qa
    if mode == "detailed":
        opening = _excerpt(content, 600)
        closing = _excerpt(content, 850, tail=True)
        if opening:
            payload["opening_excerpt"] = opening
        if closing:
            payload["closing_excerpt"] = closing
    elif mode == "compact":
        closing = _excerpt(content, 420, tail=True)
        if closing:
            payload["closing_excerpt"] = closing
    return payload


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compile_manuscript_capsules(
    chapters: Iterable[Any],
    *,
    max_chars: int = 70_000,
) -> ManuscriptCapsules:
    """Represent every chapter in a bounded structural-review payload.

    Developmental planning needs whole-book coverage, not every line of prose. The compiler first
    keeps QA signals plus opening/closing excerpts, then degrades detail uniformly across all
    chapters before ever dropping a chapter from the structural map.
    """

    chapter_list = list(chapters)
    budget = max(30_000, int(max_chars))
    if not chapter_list:
        return ManuscriptCapsules(chapters=[], output_chars=2, mode="detailed")

    for mode in ("detailed", "compact", "minimal"):
        payload = [_capsule(chapter, mode=mode) for chapter in chapter_list]
        size = _json_chars(payload)
        if size <= budget:
            return ManuscriptCapsules(chapters=payload, output_chars=size, mode=mode)

    # Extremely long chapter counts still retain one structural record per chapter. Trim the two
    # free-text summaries proportionally until the payload fits the configured ceiling.
    target_per_chapter = max(40, ((budget // max(1, len(chapter_list))) - 220) // 5)
    for per_chapter_text in range(target_per_chapter, 39, -10):
        payload = []
        for chapter in chapter_list:
            continuity = _as_dict(getattr(chapter, "continuity_update", None))
            story_turn = _as_dict(continuity.get("story_turn"))
            payload.append(
                {
                    "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
                    "title": _text(getattr(chapter, "title", ""), 100),
                    "outline_summary": _text(getattr(chapter, "outline_summary", ""), per_chapter_text),
                    "summary": _text(getattr(chapter, "summary", ""), per_chapter_text),
                    "story_turn": {
                        key: _text(story_turn.get(key, ""), per_chapter_text)
                        for key in ("irreversible_change", "permanent_consequence", "state_after")
                        if story_turn.get(key)
                    },
                }
            )
        size = _json_chars(payload)
        if size <= budget:
            return ManuscriptCapsules(chapters=payload, output_chars=size, mode="minimal")

    payload = [
        {
            "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
            "title": _text(getattr(chapter, "title", ""), 80),
            "summary": _text(getattr(chapter, "summary", ""), 120),
        }
        for chapter in chapter_list
    ]
    return ManuscriptCapsules(chapters=payload, output_chars=_json_chars(payload), mode="minimal")
