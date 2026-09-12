from __future__ import annotations

import json
from types import SimpleNamespace

from novel_generator.services.longform_runtime import _rewrite_manuscript_qa
from novel_generator.services.manuscript_qa_context import compile_manuscript_qa_capsules


def _chapters(count: int = 64) -> list[SimpleNamespace]:
    chapters: list[SimpleNamespace] = []
    for number in range(1, count + 1):
        chapters.append(
            SimpleNamespace(
                chapter_number=number,
                title=f"Chapter {number}",
                outline_summary=(f"Outline pressure {number} around the archive and trust fracture. " * 12).strip(),
                summary=(f"Chapter {number} changes leverage and leaves a permanent consequence. " * 20).strip(),
                word_count=2400 + number,
                continuity_update={
                    "chapter_outcome": f"Outcome {number}",
                    "story_turn": {
                        "irreversible_change": f"Irreversible change {number}. " * 6,
                        "protagonist_choice": f"Choice {number}.",
                        "permanent_consequence": f"Permanent consequence {number}. " * 6,
                        "why_this_chapter_cannot_be_cut": f"Structural reason {number}.",
                        "state_after": f"State after {number}.",
                    },
                    "entity_state_changes": {f"Entity {number}": f"State change {number}."},
                    "ideology_shift_notes": {"Iris": f"Ideology shift {number}."},
                    "trust_fractures": {"Iris/Tarin": f"Trust fracture state {number}."},
                    "emotional_open_loops": {"Iris": f"Emotional loop {number}."},
                    "side_character_decisions": {"Tarin": [f"Decision {number}."]},
                    "genre_state": {"pressure": f"Pressure {number}."},
                    "system_state_transitions": [
                        {
                            "system_name": "Archive",
                            "previous_state": f"State {number - 1}",
                            "new_state": f"State {number}",
                            "cause": f"Cause {number}",
                            "chapter_number": number,
                        }
                    ],
                },
                qa_notes={
                    "revision_required": number % 4 == 0,
                    "forward_motion_score": 7,
                    "repetition_risk_score": 4,
                    "technical_escalation_fatigue_score": 3,
                    "warnings": [f"Warning {number} about repeated machinery."],
                    "focus": [f"Focus {number} on human consequence."],
                },
            )
        )
    return chapters


def test_qa_capsules_keep_all_64_chapters_within_budget() -> None:
    packet = compile_manuscript_qa_capsules(_chapters(), max_chars=30_000)

    assert len(packet.chapters) == 64
    assert packet.output_chars <= 30_000
    assert [row["chapter_number"] for row in packet.chapters] == list(range(1, 65))
    assert packet.mode in {"detailed", "compact", "minimal"}


def test_qa_prompt_rewrite_replaces_repetitive_full_continuity_payload() -> None:
    chapters = _chapters(12)
    original_payload = [
        {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "summary": chapter.summary,
            "word_count": chapter.word_count,
            "continuity_update": chapter.continuity_update,
            "story_turn": chapter.continuity_update["story_turn"],
            "qa_notes": chapter.qa_notes,
        }
        for chapter in chapters
    ]
    messages = [
        {
            "role": "user",
            "content": (
                "Review manuscript.\n\n"
                "Chapter summaries and QA notes:\n"
                + json.dumps(original_payload, indent=2)
                + "\n\nReturn a JSON object with exactly these keys:\n{\"overall_verdict\":\"string\"}"
            ),
        }
    ]

    rewritten = _rewrite_manuscript_qa(messages, chapters, max_chars=30_000)
    content = rewritten[0]["content"]

    assert len(content) < len(messages[0]["content"])
    assert '"chapter_number":1' in content
    assert '"chapter_number":12' in content
    assert "continuity_signals" in content
    assert "continuity_update" not in content
    assert "Return a JSON object with exactly these keys:" in content
