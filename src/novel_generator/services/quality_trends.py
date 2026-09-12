from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from statistics import mean
from typing import Any, Iterable


_SCORE_FIELDS = (
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
    "irreversibility_score",
    "choice_clarity_score",
)


@dataclass(frozen=True, slots=True)
class QualityTrendAudit:
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


def _plan(chapter: Any) -> dict[str, Any]:
    value = getattr(chapter, "plan", None)
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return _as_dict(value)


def _word_count(chapter: Any) -> int:
    try:
        stored = int(getattr(chapter, "word_count", 0) or 0)
    except (TypeError, ValueError):
        stored = 0
    if stored > 0:
        return stored
    return len(str(getattr(chapter, "content", "") or "").split())


def _numeric_score(qa: dict[str, Any], key: str) -> float | None:
    value = qa.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _segment_average(rows: list[Any], key: str) -> float | None:
    values = [
        score
        for chapter in rows
        if (score := _numeric_score(_as_dict(getattr(chapter, "qa_notes", None)), key)) is not None
    ]
    return round(mean(values), 2) if values else None


def _longest_run(values: list[str]) -> tuple[str, int]:
    best_value = ""
    best = 0
    current_value = ""
    current = 0
    for value in values:
        if not value:
            current_value = ""
            current = 0
            continue
        if value == current_value:
            current += 1
        else:
            current_value = value
            current = 1
        if current > best:
            best = current
            best_value = value
    return best_value, best


def _warning_texts(chapter: Any) -> list[str]:
    qa = _as_dict(getattr(chapter, "qa_notes", None))
    result: list[str] = []
    for key in ("warnings", "soft_warnings", "blocking_issues"):
        items = qa.get(key)
        if isinstance(items, list):
            result.extend(" ".join(str(item).split()) for item in items if str(item).strip())
    return result


def compile_quality_trend_audit(chapters: Iterable[Any]) -> QualityTrendAudit:
    """Summarize gradual manuscript-quality drift that per-chapter checks can miss.

    The audit consumes only already persisted chapter metadata/QA. It makes no model call and does
    not rewrite chapter state. Signals are intentionally descriptive; they tell manuscript QA where
    to inspect rather than automatically deciding that a stylistic shift is wrong.
    """

    rows = sorted(
        list(chapters),
        key=lambda chapter: int(getattr(chapter, "chapter_number", 0) or 0),
    )
    if not rows:
        return QualityTrendAudit(payload={})

    segment_size = max(1, len(rows) // 3)
    first = rows[:segment_size]
    last = rows[-segment_size:]
    word_counts = [_word_count(chapter) for chapter in rows]
    first_words = round(mean(_word_count(chapter) for chapter in first), 1)
    last_words = round(mean(_word_count(chapter) for chapter in last), 1)
    length_delta_pct = round((last_words - first_words) / first_words * 100, 1) if first_words else 0.0

    score_trends: dict[str, Any] = {}
    risk_flags: list[str] = []
    for key in _SCORE_FIELDS:
        first_avg = _segment_average(first, key)
        last_avg = _segment_average(last, key)
        overall = _segment_average(rows, key)
        if overall is None:
            continue
        delta = round(last_avg - first_avg, 2) if first_avg is not None and last_avg is not None else None
        score_trends[key] = {
            "overall_average": overall,
            "first_third_average": first_avg,
            "last_third_average": last_avg,
            "last_vs_first_delta": delta,
        }
        if delta is not None and delta <= -1.25:
            risk_flags.append(f"{key} drops {abs(delta):.2f} points from the first third to the last third.")

    modes = [str(_plan(chapter).get("chapter_mode", "") or "").strip() for chapter in rows]
    hooks = [str(_as_dict(getattr(chapter, "qa_notes", None)).get("ending_hook_type", "") or "").strip() for chapter in rows]
    mode_value, mode_run = _longest_run(modes)
    hook_value, hook_run = _longest_run(hooks)
    if mode_run >= 3:
        risk_flags.append(f"Chapter mode '{mode_value}' repeats for {mode_run} consecutive chapters.")
    if hook_run >= 3:
        risk_flags.append(f"Ending hook type '{hook_value}' repeats for {hook_run} consecutive chapters.")
    if length_delta_pct <= -25:
        risk_flags.append(f"Average chapter length falls {abs(length_delta_pct):.1f}% from the first third to the last third.")

    revision_required = [
        int(getattr(chapter, "chapter_number", 0) or 0)
        for chapter in rows
        if bool(_as_dict(getattr(chapter, "qa_notes", None)).get("revision_required"))
    ]
    warning_counts = Counter(text for chapter in rows for text in _warning_texts(chapter))
    repeated_warnings = [
        {"warning": warning, "count": count}
        for warning, count in warning_counts.most_common(8)
        if count >= 2
    ]
    if repeated_warnings:
        risk_flags.append("One or more QA warnings recur across multiple chapters instead of being retired by revision.")

    payload = {
        "_quality_trend_audit": {
            "chapter_count": len(rows),
            "segment_size": segment_size,
            "note": "Deterministic trend audit from persisted chapter QA/state; signals require editorial interpretation.",
        },
        "length_trend": {
            "total_words": sum(word_counts),
            "mean_chapter_words": round(mean(word_counts), 1),
            "first_third_mean_words": first_words,
            "last_third_mean_words": last_words,
            "last_vs_first_percent": length_delta_pct,
            "shortest_chapter_words": min(word_counts),
            "longest_chapter_words": max(word_counts),
        },
        "score_trends": score_trends,
        "structural_variety": {
            "unique_chapter_modes": len({mode for mode in modes if mode}),
            "longest_same_mode_run": {"mode": mode_value, "chapters": mode_run},
            "unique_ending_hook_types": len({hook for hook in hooks if hook}),
            "longest_same_ending_hook_run": {"type": hook_value, "chapters": hook_run},
        },
        "revision_required_chapters": revision_required,
        "repeated_warnings": repeated_warnings,
        "risk_flags": risk_flags,
        "editorial_rules": [
            "Treat a trend as a place to inspect, not proof that intentional late-book compression or tonal change is wrong.",
            "Prioritize sustained multi-chapter degradation over one isolated low score.",
            "When repairing drift, preserve causal state and character consequences instead of normalizing every chapter to the same rhythm.",
        ],
    }
    return QualityTrendAudit(payload=payload)
