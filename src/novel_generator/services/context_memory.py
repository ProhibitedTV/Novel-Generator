from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-']{1,}")
_STOP_WORDS = {
    "about", "after", "again", "against", "also", "because", "before", "being", "between",
    "chapter", "could", "current", "does", "during", "each", "from", "have", "into", "itself",
    "more", "must", "novel", "only", "other", "over", "same", "should", "story", "than", "that",
    "their", "there", "these", "they", "this", "through", "under", "very", "what", "when", "where",
    "which", "while", "with", "would",
}


@dataclass(frozen=True, slots=True)
class MemoryPacket:
    payload: dict[str, Any]
    source_chars: int
    output_chars: int
    compacted: bool


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


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def _terms(value: Any) -> set[str]:
    return {
        term
        for term in _WORD_RE.findall(_flatten(value).lower())
        if len(term) >= 3 and term not in _STOP_WORDS
    }


def _clip_text(value: Any, limit: int = 520) -> Any:
    if not isinstance(value, str):
        return value
    rendered = " ".join(value.split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(1, limit - 1)].rstrip() + "…"


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return _clip_text(value)


def _score(value: Any, focus_terms: set[str]) -> int:
    if not focus_terms:
        return 0
    candidate_terms = _terms(value)
    overlap = candidate_terms & focus_terms
    score = len(overlap) * 4
    if overlap:
        score += min(8, sum(1 for term in overlap if len(term) >= 6))
    return score


def _select_mapping(value: Any, focus_terms: set[str], limit: int) -> dict[str, Any]:
    mapping = value if isinstance(value, dict) else {}
    ranked: list[tuple[int, int, str, Any]] = []
    for index, (key, item) in enumerate(mapping.items()):
        score = _score({key: item}, focus_terms)
        ranked.append((score, index, str(key), item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    chosen = ranked[: max(0, limit)]
    chosen.sort(key=lambda row: row[1])
    return {key: _sanitize(item) for _, _, key, item in chosen}


def _select_list(value: Any, focus_terms: set[str], limit: int, *, keep_recent: int = 0) -> list[Any]:
    items = list(value) if isinstance(value, list) else []
    if not items or limit <= 0:
        return []

    selected_indices: set[int] = set()
    if keep_recent:
        selected_indices.update(range(max(0, len(items) - keep_recent), len(items)))

    ranked = sorted(
        ((_score(item, focus_terms), index) for index, item in enumerate(items)),
        key=lambda row: (row[0], row[1]),
        reverse=True,
    )
    for score, index in ranked:
        if len(selected_indices) >= limit:
            break
        if score > 0 or not selected_indices:
            selected_indices.add(index)

    if len(selected_indices) < limit:
        for index in range(len(items) - 1, -1, -1):
            if len(selected_indices) >= limit:
                break
            selected_indices.add(index)

    return [_sanitize(items[index]) for index in sorted(selected_indices)[-limit:]]


def _entity_name(entity: Any) -> str:
    if isinstance(entity, dict):
        return str(entity.get("name", "") or "")
    return str(getattr(entity, "name", "") or "")


def _select_entities(value: Any, focus_terms: set[str], limit: int) -> list[Any]:
    entities = list(value) if isinstance(value, list) else []
    ranked: list[tuple[int, int, Any]] = []
    for index, entity in enumerate(entities):
        name = _entity_name(entity)
        score = _score(entity, focus_terms)
        if name and _terms(name) & focus_terms:
            score += 12
        ranked.append((score, index, entity))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    chosen = ranked[: max(0, limit)]
    chosen.sort(key=lambda row: row[1])
    return [_sanitize(item) for _, _, item in chosen]


def _json_chars(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _try_add(payload: dict[str, Any], key: str, value: Any, budget: int) -> bool:
    if value in ({}, [], "", None):
        return False
    candidate = {**payload, key: value}
    if _json_chars(candidate) > budget:
        return False
    payload[key] = value
    return True


def _fit_sequence(payload: dict[str, Any], key: str, value: list[Any], budget: int) -> None:
    if not value:
        return
    for start in range(0, len(value)):
        candidate = value[start:]
        if _try_add(payload, key, candidate, budget):
            return


def _fit_mapping(payload: dict[str, Any], key: str, value: dict[str, Any], budget: int) -> None:
    if not value:
        return
    items = list(value.items())
    for start in range(0, len(items)):
        candidate = dict(items[start:])
        if _try_add(payload, key, candidate, budget):
            return


def compile_memory_packet(
    continuity_ledger: Any,
    *,
    focus: Any = None,
    max_chars: int = 14_000,
) -> MemoryPacket:
    """Build a bounded, relevance-ranked read-only view of the durable continuity ledger.

    The stored ledger is never mutated. Small ledgers pass through unchanged. Once a ledger grows
    beyond ``max_chars``, high-value current state is retained first and historical material is
    selected by lexical relevance plus recency.
    """

    ledger = _as_dict(continuity_ledger)
    source_chars = _json_chars(ledger)
    budget = max(4_000, int(max_chars))
    if source_chars <= budget:
        return MemoryPacket(payload=ledger, source_chars=source_chars, output_chars=source_chars, compacted=False)

    focus_terms = _terms(focus)
    payload: dict[str, Any] = {
        "_memory_packet": {
            "version": 1,
            "compacted": True,
            "source_chars": source_chars,
            "budget_chars": budget,
            "note": (
                "Relevance-ranked read view of the complete continuity ledger. Omitted historical facts remain canon; "
                "do not contradict them or assume omission means resolution."
            ),
        },
        "current_patch_status": _clip_text(ledger.get("current_patch_status", ""), 700),
        "world_state": _clip_text(ledger.get("world_state", ""), 900),
        "genre_state": _sanitize(ledger.get("genre_state", {})),
    }

    sections: list[tuple[str, Any, str]] = [
        ("open_promises_by_name", _select_mapping(ledger.get("open_promises_by_name"), focus_terms, 12), "map"),
        ("character_states", _select_mapping(ledger.get("character_states"), focus_terms, 10), "map"),
        ("open_threads", _select_list(ledger.get("open_threads"), focus_terms, 10, keep_recent=4), "list"),
        ("trust_fractures", _select_mapping(ledger.get("trust_fractures"), focus_terms, 8), "map"),
        ("emotional_open_loops", _select_mapping(ledger.get("emotional_open_loops"), focus_terms, 8), "map"),
        ("memory_damage", _select_mapping(ledger.get("memory_damage"), focus_terms, 6), "map"),
        ("entity_state_changes", _select_mapping(ledger.get("entity_state_changes"), focus_terms, 8), "map"),
        ("system_state_by_name", _select_mapping(ledger.get("system_state_by_name"), focus_terms, 8), "map"),
        ("timeline", _select_list(ledger.get("timeline"), focus_terms, 8, keep_recent=5), "list"),
        (
            "system_state_transitions",
            _select_list(ledger.get("system_state_transitions"), focus_terms, 8, keep_recent=5),
            "list",
        ),
        ("active_entities", _select_entities(ledger.get("active_entities"), focus_terms, 8), "list"),
        ("ideology_state_by_character", _select_mapping(ledger.get("ideology_state_by_character"), focus_terms, 8), "map"),
        ("side_character_decisions", _select_mapping(ledger.get("side_character_decisions"), focus_terms, 7), "map"),
        (
            "civilian_pressure_points",
            _select_list(ledger.get("civilian_pressure_points"), focus_terms, 6, keep_recent=3),
            "list",
        ),
        ("resolved_threads", _select_list(ledger.get("resolved_threads"), focus_terms, 4, keep_recent=1), "list"),
    ]

    for key, value, kind in sections:
        if _try_add(payload, key, value, budget):
            continue
        if kind == "map" and isinstance(value, dict):
            _fit_mapping(payload, key, value, budget)
        elif kind == "list" and isinstance(value, list):
            _fit_sequence(payload, key, value, budget)

    output_chars = _json_chars(payload)
    return MemoryPacket(payload=payload, source_chars=source_chars, output_chars=output_chars, compacted=True)
