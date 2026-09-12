from __future__ import annotations

from types import SimpleNamespace

from novel_generator.services.chapter_recall import compile_chapter_recall
from novel_generator.services.recall_runtime import _wrap_builder


def _chapter(number: int, summary: str, consequence: str) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        title=f"Chapter {number}",
        outline_summary=summary,
        summary=summary,
        continuity_update={
            "story_turn": {
                "irreversible_change": consequence,
                "protagonist_choice": f"Choice {number}",
                "permanent_consequence": consequence,
                "state_after": f"State {number}",
            }
        },
    )


def _run() -> SimpleNamespace:
    chapters = [
        _chapter(1, "Iris discovers a red cipher key in the abandoned observatory.", "The red cipher key is now in Iris's possession."),
        _chapter(2, "Iris hides the red cipher key from Tarin and leaves the observatory.", "Tarin does not know Iris kept the key."),
        _chapter(3, "The council imposes ration controls after a transit failure.", "Ration controls divide the district."),
        _chapter(4, "Mara negotiates with the dockworkers over fuel access.", "Mara owes the dockworkers a favor."),
        _chapter(5, "Iris survives a public hearing about the archive breach.", "The hearing makes Iris publicly accountable."),
        _chapter(6, "Iris and Mara repair their political alliance.", "Their alliance becomes conditional."),
        _chapter(7, "The Directorate searches the lower district for contraband.", "Search teams now control the lower district."),
        _chapter(8, "Iris evades the search teams and reaches Tarin.", "Iris and Tarin are reunited."),
    ]
    run = SimpleNamespace(chapters=chapters)
    for chapter in chapters:
        chapter.run = run
    return run


def test_chapter_recall_keeps_recent_context_and_retrieves_old_callback() -> None:
    packet = compile_chapter_recall(
        _run(),
        9,
        focus={
            "objective": "Use the red cipher key to unlock the observatory transmission before the Directorate arrives.",
            "reveal": "Tarin learns Iris kept the cipher key from him.",
        },
        max_chars=9_000,
        recent_chapters=2,
        relevant_chapters=3,
    ).payload

    numbers = [row["chapter_number"] for row in packet["chapters"]]
    assert 7 in numbers and 8 in numbers
    assert 1 in numbers or 2 in numbers
    callback_rows = [row for row in packet["chapters"] if row["selection"] == "relevant_callback"]
    assert any(row["chapter_number"] in {1, 2} for row in callback_rows)
    assert packet["_chapter_recall"]["relevant_callback_count"] >= 1


def test_recall_wrapper_injects_selected_old_chapter_without_full_history() -> None:
    run = _run()
    chapter = SimpleNamespace(
        chapter_number=9,
        title="The Cipher Opens",
        outline_summary="Iris uses the red cipher key while Tarin discovers she kept it secret.",
        summary="",
        run=run,
    )

    def build_chapter_plan_messages(run: object, chapter: object, outline_entry: dict) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Plan the chapter."},
            {"role": "user", "content": "Current chapter outline:\n{}"},
        ]

    wrapped = _wrap_builder(
        build_chapter_plan_messages,
        max_chars=9_000,
        recent_chapters=2,
        relevant_chapters=3,
    )
    messages = wrapped(
        run,
        chapter,
        {
            "objective": "Use the red cipher key to unlock the observatory transmission.",
            "reveal": "Tarin learns Iris kept the key.",
        },
    )
    prompt = messages[-1]["content"]

    assert "Long-range chapter recall" in prompt
    assert "red cipher key" in prompt
    assert '"chapter_number":1' in prompt or '"chapter_number":2' in prompt
    assert '"chapter_number":7' in prompt and '"chapter_number":8' in prompt
