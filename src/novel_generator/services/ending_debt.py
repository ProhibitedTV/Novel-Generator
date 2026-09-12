from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable


_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_\-']{2,}")
_STOP = {
    "about", "after", "again", "because", "before", "being", "book", "chapter", "could", "ending",
    "final", "from", "have", "into", "must", "novel", "only", "other", "should", "story", "than", "that",
    "their", "there", "these", "they", "this", "through", "what", "when", "where", "which", "while", "with",
    "would",
}


@dataclass(frozen=True, slots=True)
class EndingDebtAudit:
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


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value or "")


def _terms(value: Any) -> set[str]:
    return {term for term in _TERM_RE.findall(_flatten(value).lower()) if term not in _STOP}


def _ending_promise(story_bible: dict[str, Any]) -> str:
    for key in ("ending_promise", "ending_contract", "final_promise"):
        value = story_bible.get(key)
        if value:
            return _clip(value, 700)
    # Older story bibles often encode the promise in an act plan rather than a dedicated field.
    act_plan = story_bible.get("act_plan")
    if isinstance(act_plan, list) and act_plan:
        return _clip(act_plan[-1], 700)
    return ""


def _rank_central_candidates(
    ending_promise: str,
    open_threads: list[str],
    open_promises: dict[str, Any],
) -> list[dict[str, Any]]:
    focus = _terms(ending_promise)
    if not focus:
        return []
    rows: list[tuple[int, str, str]] = []
    for thread in open_threads:
        overlap = focus & _terms(thread)
        if overlap:
            rows.append((len(overlap), "open_thread", _clip(thread)))
    for name, value in open_promises.items():
        rendered = f"{name}: {value}"
        overlap = focus & _terms(rendered)
        if overlap:
            rows.append((len(overlap), "open_promise", _clip(rendered)))
    rows.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [
        {"kind": kind, "text": text, "ending_promise_term_overlap": score}
        for score, kind, text in rows[:8]
        if score >= 2
    ]


def compile_ending_debt_audit(run: Any, chapters: Iterable[Any] | None = None) -> EndingDebtAudit:
    """Describe live story debt still present at the manuscript checkpoint.

    This is an editorial signal, not an automatic failure gate: scars, aftermath, and sequel residue can
    intentionally remain open. The important requirement is that the final QA explicitly distinguishes
    intentional residue from accidentally abandoned central promises.
    """

    if run is None:
        return EndingDebtAudit(payload={})
    ledger = _as_dict(getattr(run, "continuity_ledger", None))
    bible = _as_dict(getattr(run, "story_bible", None))
    rows = sorted(
        list(chapters if chapters is not None else (getattr(run, "chapters", None) or [])),
        key=lambda chapter: int(getattr(chapter, "chapter_number", 0) or 0),
    )
    ending_promise = _ending_promise(bible)
    open_threads = [_clip(item) for item in list(ledger.get("open_threads") or []) if str(item).strip()]
    open_promises = {
        str(key): _clip(value)
        for key, value in dict(ledger.get("open_promises_by_name") or {}).items()
        if str(key).strip() or str(value).strip()
    }
    trust = {str(key): _clip(value) for key, value in dict(ledger.get("trust_fractures") or {}).items() if str(value).strip()}
    emotional = {
        str(key): _clip(value)
        for key, value in dict(ledger.get("emotional_open_loops") or {}).items()
        if str(value).strip()
    }
    memory_damage = {
        str(key): _clip(value)
        for key, value in dict(ledger.get("memory_damage") or {}).items()
        if str(value).strip()
    }
    civilian = [_clip(item) for item in list(ledger.get("civilian_pressure_points") or []) if str(item).strip()]
    central = _rank_central_candidates(ending_promise, open_threads, open_promises)

    final_chapter: dict[str, Any] = {}
    if rows:
        chapter = rows[-1]
        continuity = _as_dict(getattr(chapter, "continuity_update", None))
        turn = _as_dict(continuity.get("story_turn"))
        final_chapter = {
            "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
            "title": _clip(getattr(chapter, "title", ""), 160),
            "summary": _clip(getattr(chapter, "summary", ""), 700),
            "chapter_outcome": _clip(continuity.get("chapter_outcome", ""), 500),
            "state_after": _clip(turn.get("state_after", ""), 500),
            "permanent_consequence": _clip(turn.get("permanent_consequence", ""), 500),
        }

    active_count = (
        len(open_threads)
        + len(open_promises)
        + len(trust)
        + len(emotional)
        + len(memory_damage)
        + len(civilian)
    )
    payload: dict[str, Any] = {
        "_ending_debt_audit": {
            "active_live_items": active_count,
            "central_candidate_count": len(central),
            "status": "checkpoint_clear" if active_count == 0 else "requires_editorial_review",
            "note": (
                "Live continuity state remaining at the end checkpoint. Remaining items are not automatically defects; "
                "final QA must distinguish intentional aftermath/sequel residue from abandoned central promises."
            ),
        },
        "ending_promise": ending_promise,
        "final_chapter": final_chapter,
        "remaining_live_state": {
            "open_threads": open_threads[:12],
            "open_promises_by_name": dict(list(open_promises.items())[:12]),
            "trust_fractures": dict(list(trust.items())[:8]),
            "emotional_open_loops": dict(list(emotional.items())[:8]),
            "memory_damage": dict(list(memory_damage.items())[:6]),
            "civilian_pressure_points": civilian[:8],
        },
        "central_ending_debt_candidates": central,
        "editorial_rules": [
            "Confirm the story-bible ending promise is paid off on page, not merely implied by a sequel hook or summary.",
            "Classify every central remaining promise/thread as resolved, intentionally transformed into aftermath, or accidentally abandoned.",
            "Do not require wounds, political consequences, grief, or relationship scars to vanish merely to make the ledger empty.",
            "A sequel-facing question may remain only after the current novel's primary conflict and emotional contract have received closure.",
            "If a final prose excerpt contradicts the stored continuity checkpoint, treat that as a reconciliation risk rather than trusting metadata blindly.",
        ],
    }
    return EndingDebtAudit(payload=payload)


def ending_debt_note(audit: EndingDebtAudit) -> str:
    payload = audit.payload
    meta = _as_dict(payload.get("_ending_debt_audit"))
    count = int(meta.get("active_live_items", 0) or 0)
    central = int(meta.get("central_candidate_count", 0) or 0)
    if count <= 0:
        return "Deterministic ending-debt audit found no remaining live continuity items at the final checkpoint."
    return (
        f"Deterministic ending-debt audit found {count} live continuity item(s) at the final checkpoint"
        + (f", including {central} candidate(s) overlapping the story-bible ending promise" if central else "")
        + ". Confirm each is intentional aftermath/sequel residue or repair it if it represents abandoned central story debt."
    )
