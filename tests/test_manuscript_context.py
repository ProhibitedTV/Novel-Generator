from __future__ import annotations

import json
from types import SimpleNamespace

from novel_generator.services.context_runtime import _rewrite_manuscript_chapters
from novel_generator.services.manuscript_context import compile_manuscript_capsules


def _chapters(count: int = 64) -> list[SimpleNamespace]:
    chapters: list[SimpleNamespace] = []
    for index in range(1, count + 1):
        chapters.append(
            SimpleNamespace(
                chapter_number=index,
                title=f"Chapter {index}",
                outline_summary=(f"Iris pursues objective {index} while Tarin complicates it. " * 20).strip(),
                summary=(f"Chapter {index} permanently changes leverage around the vault. " * 30).strip(),
                word_count=2_500,
                content=(f"Opening scene material for chapter {index}. " * 300)
                + (f" Closing consequence for chapter {index}." * 300),
                continuity_update={
                    "story_turn": {
                        "irreversible_change": f"Irreversible change {index}. " * 8,
                        "protagonist_choice": f"Iris chooses route {index}.",
                        "choice_alternatives": [f"Alternative {index}."],
                        "permanent_consequence": f"Permanent consequence {index}. " * 8,
                        "why_this_chapter_cannot_be_cut": f"Structural reason {index}.",
                        "state_before": f"State before {index}.",
                        "state_after": f"State after {index}. " * 8,
                    }
                },
                qa_notes={
                    "revision_required": index % 3 == 0,
                    "forward_motion_score": 7,
                    "repetition_risk_score": 4,
                    "warnings": [f"Warning {index} about repeated mechanics."],
                    "focus": [f"Focus {index} on human consequence."],
                },
            )
        )
    return chapters


def test_manuscript_capsules_keep_every_chapter_inside_budget() -> None:
    chapters = _chapters()

    packet = compile_manuscript_capsules(chapters, max_chars=30_000)

    assert len(packet.chapters) == 64
    assert packet.output_chars <= 30_000
    assert [item["chapter_number"] for item in packet.chapters] == list(range(1, 65))
    assert packet.mode in {"detailed", "compact", "minimal"}


def test_developmental_prompt_rewrite_removes_full_manuscript_prose() -> None:
    chapters = _chapters(8)
    original_payload = [
        {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "content": chapter.content,
        }
        for chapter in chapters
    ]
    messages = [
        {
            "role": "user",
            "content": (
                "Create a developmental plan.\n\n"
                "Full manuscript chapters:\n"
                + json.dumps(original_payload, indent=2)
                + "\n\nReturn a JSON object with exactly these keys:\n{\"chapter_actions\": []}"
            ),
        }
    ]

    rewritten = _rewrite_manuscript_chapters(messages, chapters, max_chars=30_000)
    content = rewritten[0]["content"]

    assert len(content) < len(messages[0]["content"])
    assert "opening_excerpt" in content or "closing_excerpt" in content or "story_turn" in content
    assert chapters[0].content not in content
    assert "Return a JSON object with exactly these keys:" in content
