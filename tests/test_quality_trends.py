from __future__ import annotations

import json
from types import SimpleNamespace

from novel_generator.services.longform_runtime import _inject_quality_trend_audit
from novel_generator.services.quality_trends import compile_quality_trend_audit


def _chapter(number: int, *, score: float, words: int, mode: str, hook: str, warning: str = "") -> SimpleNamespace:
    warnings = [warning] if warning else []
    return SimpleNamespace(
        chapter_number=number,
        word_count=words,
        content="",
        plan=json.dumps({"chapter_mode": mode}),
        qa_notes={
            "forward_motion_score": score,
            "emotional_depth_score": score,
            "repetition_risk_score": score,
            "ending_hook_type": hook,
            "warnings": warnings,
            "revision_required": number == 9,
        },
    )


def _chapters() -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for number in range(1, 10):
        if number <= 3:
            rows.append(_chapter(number, score=9.0, words=2400, mode=f"mode_{number}", hook=f"hook_{number}"))
        elif number <= 6:
            rows.append(_chapter(number, score=8.0, words=2200, mode=f"mode_{number}", hook=f"hook_{number}"))
        else:
            rows.append(
                _chapter(
                    number,
                    score=6.5,
                    words=1600,
                    mode="investigation",
                    hook="document_reveal",
                    warning="Scenes are starting to resolve through repeated archive explanations.",
                )
            )
    return rows


def test_quality_trend_audit_detects_gradual_late_book_drift() -> None:
    audit = compile_quality_trend_audit(_chapters()).payload

    assert audit["length_trend"]["first_third_mean_words"] == 2400.0
    assert audit["length_trend"]["last_third_mean_words"] == 1600.0
    assert audit["length_trend"]["last_vs_first_percent"] == -33.3
    assert audit["score_trends"]["forward_motion_score"]["last_vs_first_delta"] == -2.5
    assert audit["structural_variety"]["longest_same_mode_run"] == {"mode": "investigation", "chapters": 3}
    assert audit["structural_variety"]["longest_same_ending_hook_run"] == {"type": "document_reveal", "chapters": 3}
    assert audit["revision_required_chapters"] == [9]
    assert audit["repeated_warnings"][0]["count"] == 3
    assert any("forward_motion_score drops" in flag for flag in audit["risk_flags"])
    assert any("Average chapter length falls" in flag for flag in audit["risk_flags"])
    assert any("recur across multiple chapters" in flag for flag in audit["risk_flags"])


def test_quality_trend_audit_is_injected_into_manuscript_qa_prompt() -> None:
    messages = [
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": "Chapter summaries and QA notes:\n[]\n\nReturn a JSON object."},
    ]

    rewritten = _inject_quality_trend_audit(messages, _chapters())
    prompt = rewritten[-1]["content"]

    assert prompt.startswith("Whole-book quality trend audit")
    assert '"last_vs_first_percent":-33.3' in prompt
    assert '"revision_required_chapters":[9]' in prompt
