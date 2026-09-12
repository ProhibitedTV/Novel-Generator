from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-']{2,}")
_STOP = {
    "about", "after", "again", "before", "being", "chapter", "could", "from", "have", "into",
    "more", "must", "only", "other", "should", "story", "than", "that", "their", "there", "these",
    "they", "this", "through", "what", "when", "where", "which", "while", "with", "would",
}


@dataclass(frozen=True, slots=True)
class ChapterRecall:
    payload: dict[str, Any]
    output_chars: int


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


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value or "")


def _terms(value: Any) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(_flatten(value).lower())
        if token not in _STOP
    }


def _story_turn(chapter: Any) -> dict[str, str]:
    continuity = _as_dict(getattr(chapter, "continuity_update", None))
    turn = _as_dict(continuity.get("story_turn"))
    return {
        key: _clip(turn.get(key, ""), 320)
        for key in ("irreversible_change", "protagonist_choice", "permanent_consequence", "state_after")
        if turn.get(key)
    }


def _candidate_text(chapter: Any) -> str:
    return " ".join(
        [
            str(getattr(chapter, "title", "") or ""),
            str(getattr(chapter, "outline_summary", "") or ""),
            str(getattr(chapter, "summary", "") or ""),
            _flatten(_story_turn(chapter)),
        ]
    )


def _row(chapter: Any, *, selection: str, overlap: set[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": _clip(getattr(chapter, "title", ""), 160),
        "summary": _clip(getattr(chapter, "summary", ""), 760),
        "selection": selection,
    }
    outline_summary = _clip(getattr(chapter, "outline_summary", ""), 460)
    if outline_summary:
        payload["outline_summary"] = outline_summary
    turn = _story_turn(chapter)
    if turn:
        payload["story_turn"] = turn
    if overlap:
        payload["matched_terms"] = sorted(overlap)[:10]
    return payload


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compile_chapter_recall(
    run: Any,
    chapter_number: int,
    *,
    focus: Any,
    max_chars: int = 9_000,
    recent_chapters: int = 2,
    relevant_chapters: int = 4,
) -> ChapterRecall:
    """Retrieve bounded older chapter memory using deterministic lexical relevance plus recency."""

    if run is None or chapter_number <= 1:
        return ChapterRecall(payload={}, output_chars=2)

    budget = max(3_000, int(max_chars))
    recent_count = min(4, max(0, int(recent_chapters)))
    relevant_count = min(8, max(0, int(relevant_chapters)))
    prior = [
        chapter
        for chapter in list(getattr(run, "chapters", None) or [])
        if int(getattr(chapter, "chapter_number", 0) or 0) < chapter_number
        and (getattr(chapter, "summary", None) or getattr(chapter, "continuity_update", None))
    ]
    prior.sort(key=lambda chapter: int(getattr(chapter, "chapter_number", 0) or 0))
    if not prior:
        return ChapterRecall(payload={}, output_chars=2)

    focus_terms = _terms(focus)
    recent = prior[-recent_count:] if recent_count else []
    recent_numbers = {int(getattr(chapter, "chapter_number", 0) or 0) for chapter in recent}

    ranked: list[tuple[int, int, Any, set[str]]] = []
    for chapter in prior:
        number = int(getattr(chapter, "chapter_number", 0) or 0)
        if number in recent_numbers:
            continue
        candidate_terms = _terms(_candidate_text(chapter))
        overlap = candidate_terms & focus_terms
        if not overlap:
            continue
        # Prefer strong semantic overlap, then more recent old chapters when scores tie.
        score = len(overlap) * 10 + sum(2 for token in overlap if len(token) >= 7)
        ranked.append((score, number, chapter, overlap))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    relevant = ranked[:relevant_count]

    selected: list[dict[str, Any]] = []
    for chapter in recent:
        overlap = _terms(_candidate_text(chapter)) & focus_terms
        selected.append(_row(chapter, selection="recent", overlap=overlap))
    for _, _, chapter, overlap in relevant:
        selected.append(_row(chapter, selection="relevant_callback", overlap=overlap))
    selected.sort(key=lambda item: item["chapter_number"])

    payload: dict[str, Any] = {
        "_chapter_recall": {
            "chapter_number": chapter_number,
            "recent_requested": recent_count,
            "relevant_requested": relevant_count,
            "note": "Bounded read-only recall of completed chapters. Omitted chapters remain canon.",
        },
        "rules": [
            "Use an older recalled chapter only when it genuinely informs the current scene; do not recap it for the reader.",
            "Preserve recalled irreversible consequences and knowledge state instead of replaying or rediscovering them.",
            "A callback should create new meaning, leverage, emotion, or consequence rather than repeat the original beat.",
        ],
        "chapters": [],
    }
    for row in selected:
        candidate = {**payload, "chapters": [*payload["chapters"], row]}
        if _json_chars(candidate) > budget:
            continue
        payload["chapters"].append(row)

    payload["_chapter_recall"]["included"] = len(payload["chapters"])
    payload["_chapter_recall"]["relevant_callback_count"] = sum(
        1 for row in payload["chapters"] if row.get("selection") == "relevant_callback"
    )
    return ChapterRecall(payload=payload, output_chars=_json_chars(payload))
