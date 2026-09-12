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
class StoryArcAudit:
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


def _clip(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _terms(value: Any) -> set[str]:
    if isinstance(value, dict):
        text = " ".join(f"{key} {item}" for key, item in value.items())
    elif isinstance(value, (list, tuple, set)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    return {token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOP}


def _chapters(run: Any) -> list[Any]:
    rows = list(getattr(run, "chapters", None) or [])
    rows.sort(key=lambda row: int(getattr(row, "chapter_number", 0) or 0))
    return rows


def _outline(run: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in getattr(run, "outline", None) or []:
        item = _as_dict(raw)
        try:
            number = int(item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        rows.append({**item, "chapter_number": number})
    rows.sort(key=lambda row: row["chapter_number"])
    return rows


def _chapter_search_text(chapter: Any) -> str:
    continuity = _as_dict(getattr(chapter, "continuity_update", None))
    plan = getattr(chapter, "plan", "") or ""
    if isinstance(plan, str) and len(plan) > 4000:
        plan = plan[:4000]
    return " ".join(
        [
            str(getattr(chapter, "title", "") or ""),
            str(getattr(chapter, "outline_summary", "") or ""),
            str(getattr(chapter, "summary", "") or ""),
            str(plan),
            json.dumps(continuity, ensure_ascii=False, default=str),
        ]
    ).lower()


def _outline_search_text(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, default=str).lower()


def _last_touch(run: Any, chapter_number: int, terms: set[str], *, exact_phrase: str = "") -> int | None:
    if not terms and not exact_phrase:
        return None
    phrase = exact_phrase.strip().lower()
    for chapter in reversed(_chapters(run)):
        number = int(getattr(chapter, "chapter_number", 0) or 0)
        if number >= chapter_number:
            continue
        text = _chapter_search_text(chapter)
        if phrase:
            if phrase in text:
                return number
            # Character presence should not be inferred from generic thematic overlap. If a named
            # character is the tracked subject, require the actual name to appear in saved state.
            continue
        overlap = terms & _terms(text)
        minimum = 1 if len(terms) <= 2 else 2
        if len(overlap) >= minimum:
            return number
    return None


def _future_touches(run: Any, chapter_number: int, terms: set[str], *, exact_phrase: str = "", limit: int = 4) -> list[int]:
    phrase = exact_phrase.strip().lower()
    matches: list[int] = []
    for item in _outline(run):
        number = int(item.get("chapter_number", 0) or 0)
        if number <= chapter_number:
            continue
        text = _outline_search_text(item)
        if phrase:
            matched = phrase in text
        else:
            overlap = terms & _terms(text)
            minimum = 1 if len(terms) <= 2 else 2
            matched = len(overlap) >= minimum
        if matched:
            matches.append(number)
            if len(matches) >= limit:
                break
    return matches


def _matching_map(mapping: Any, name: str, *, limit: int = 4) -> dict[str, str]:
    if not isinstance(mapping, dict) or not name:
        return {}
    needle = name.lower()
    result: dict[str, str] = {}
    for key, value in mapping.items():
        if needle in str(key).lower() or needle in str(value).lower():
            result[str(key)] = _clip(value)
            if len(result) >= limit:
                break
    return result


def _character_rows(run: Any, chapter_number: int, dormant_after: int) -> list[dict[str, Any]]:
    bible = _as_dict(getattr(run, "story_bible", None))
    ledger = _as_dict(getattr(run, "continuity_ledger", None))
    agendas = list(bible.get("character_agendas") or [])
    character_states = ledger.get("character_states") if isinstance(ledger.get("character_states"), dict) else {}
    ideology = ledger.get("ideology_state_by_character") if isinstance(ledger.get("ideology_state_by_character"), dict) else {}
    emotions = ledger.get("emotional_open_loops") if isinstance(ledger.get("emotional_open_loops"), dict) else {}
    decisions = ledger.get("side_character_decisions") if isinstance(ledger.get("side_character_decisions"), dict) else {}
    trust = ledger.get("trust_fractures") if isinstance(ledger.get("trust_fractures"), dict) else {}

    rows: list[dict[str, Any]] = []
    for raw in agendas[:16]:
        agenda = _as_dict(raw)
        name = str(agenda.get("name", "") or "").strip()
        if not name:
            continue
        baseline = {
            "want": _clip(agenda.get("want", "")),
            "fear": _clip(agenda.get("fear", "")),
            "line_in_sand": _clip(agenda.get("line_in_sand", "")),
            "public_belief": _clip(agenda.get("public_belief", "")),
            "private_pressure": _clip(agenda.get("private_pressure", "")),
        }
        focus_terms = _terms({"name": name, **baseline})
        last = _last_touch(run, chapter_number, focus_terms, exact_phrase=name)
        dormant_for = max(0, chapter_number - (last or 0) - 1) if chapter_number > 1 else 0
        unresolved = bool(
            character_states.get(name)
            or ideology.get(name)
            or emotions.get(name)
            or _matching_map(trust, name)
        )
        recent_decisions = decisions.get(name, []) if isinstance(decisions.get(name), list) else []
        rows.append(
            {
                "name": name,
                "baseline": {key: value for key, value in baseline.items() if value},
                "current_state": _clip(character_states.get(name, "")),
                "ideology_state": _clip(ideology.get(name, "")),
                "emotional_open_loop": _clip(emotions.get(name, "")),
                "trust_fractures": _matching_map(trust, name),
                "recent_independent_decisions": [_clip(item) for item in recent_decisions[-3:]],
                "last_touched_chapter": last,
                "dormant_for_chapters": dormant_for,
                "upcoming_planned_touches": _future_touches(run, chapter_number, focus_terms, exact_phrase=name),
                "attention": "dormant_unresolved" if unresolved and dormant_for >= dormant_after else ("active" if unresolved else "stable"),
            }
        )
    rows.sort(key=lambda row: (row["attention"] != "dormant_unresolved", -int(row["dormant_for_chapters"]), row["name"]))
    return rows


def _subplot_sources(ledger: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    promises = ledger.get("open_promises_by_name")
    if isinstance(promises, dict):
        rows.extend(("promise", str(key), str(value)) for key, value in promises.items())
    threads = ledger.get("open_threads")
    if isinstance(threads, list):
        rows.extend(("thread", f"thread_{index + 1}", str(value)) for index, value in enumerate(threads))
    emotions = ledger.get("emotional_open_loops")
    if isinstance(emotions, dict):
        rows.extend(("emotional", str(key), str(value)) for key, value in emotions.items())
    trust = ledger.get("trust_fractures")
    if isinstance(trust, dict):
        rows.extend(("trust", str(key), str(value)) for key, value in trust.items())
    return rows


def _subplot_rows(run: Any, chapter_number: int, dormant_after: int) -> list[dict[str, Any]]:
    ledger = _as_dict(getattr(run, "continuity_ledger", None))
    rows: list[dict[str, Any]] = []
    for lane_type, label, description in _subplot_sources(ledger):
        # Subplots are often referred to by natural-language state rather than their internal key,
        # so use semantic term overlap instead of requiring the bookkeeping label to appear verbatim.
        terms = _terms({"label": label, "description": description})
        last = _last_touch(run, chapter_number, terms)
        dormant_for = max(0, chapter_number - (last or 0) - 1) if chapter_number > 1 else 0
        rows.append(
            {
                "type": lane_type,
                "label": _clip(label, 160),
                "state": _clip(description, 420),
                "last_touched_chapter": last,
                "dormant_for_chapters": dormant_for,
                "upcoming_planned_touches": _future_touches(run, chapter_number, terms),
                "attention": "dormant_unresolved" if dormant_for >= dormant_after else "active",
            }
        )
    rows.sort(key=lambda row: (row["attention"] != "dormant_unresolved", -int(row["dormant_for_chapters"]), row["type"], row["label"]))
    return rows


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compile_story_arc_audit(
    run: Any,
    chapter_number: int,
    *,
    max_chars: int = 12_000,
    dormant_after: int = 5,
) -> StoryArcAudit:
    """Build a bounded read-only audit of character arcs and unresolved subplot lanes.

    The audit derives from the durable story bible, continuity ledger, completed chapter checkpoints,
    and future outline. It does not invent or persist canon. Its purpose is to keep long books from
    silently abandoning supporting-character arcs or unresolved promises for dozens of chapters.
    """

    if run is None or chapter_number <= 0:
        return StoryArcAudit(payload={}, output_chars=2)

    budget = max(4_000, int(max_chars))
    dormant = max(2, int(dormant_after))
    characters = _character_rows(run, chapter_number, dormant)
    subplots = _subplot_rows(run, chapter_number, dormant)
    total_chapters = max(int(getattr(run, "requested_chapters", 0) or 0), len(_outline(run)), chapter_number)

    header = {
        "_story_arc_audit": {
            "chapter_number": chapter_number,
            "total_chapters": total_chapters,
            "dormant_after_chapters": dormant,
            "note": "Read-only audit from persisted canon/state. Dormant does not mean resolved.",
        },
        "rules": [
            "Do not silently abandon unresolved character or subplot state; either advance it, deliberately defer it, or preserve a future setup.",
            "Do not force every dormant lane into the current chapter. Prefer one or two relevant reactivations over checklist writing.",
            "A supporting character should change the protagonist's options through an independent choice, not merely restate the theme.",
            "Do not resolve future outline commitments early just to clear an open lane.",
        ],
    }

    # Start with the highest-risk rows and add detail only while the packet fits. Every omitted row
    # remains present in durable state and can reappear when it becomes relevant or more dormant.
    payload = {**header, "character_arcs": [], "subplot_lanes": []}
    for row in characters:
        candidate = {**payload, "character_arcs": [*payload["character_arcs"], row]}
        if _json_chars(candidate) > budget:
            break
        payload["character_arcs"].append(row)
    for row in subplots:
        candidate = {**payload, "subplot_lanes": [*payload["subplot_lanes"], row]}
        if _json_chars(candidate) > budget:
            break
        payload["subplot_lanes"].append(row)

    payload["_story_arc_audit"]["character_rows_included"] = len(payload["character_arcs"])
    payload["_story_arc_audit"]["subplot_rows_included"] = len(payload["subplot_lanes"])
    payload["_story_arc_audit"]["dormant_character_count"] = sum(1 for row in characters if row["attention"] == "dormant_unresolved")
    payload["_story_arc_audit"]["dormant_subplot_count"] = sum(1 for row in subplots if row["attention"] == "dormant_unresolved")
    return StoryArcAudit(payload=payload, output_chars=_json_chars(payload))
