from __future__ import annotations

from types import SimpleNamespace

from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services.publication_guard_runtime import (
    compile_publication_blockers,
    guard_final_edit_regressions,
)


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _Pipeline:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_event(self, session, run, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _chapter(number: int, words: int = 2200) -> SimpleNamespace:
    content = (f"Iris crosses archive corridor {number} carrying evidence " * (words // 7)).strip()
    return SimpleNamespace(
        chapter_number=number,
        title=f"Chapter {number}",
        content=content,
        word_count=len(content.split()),
        summary="Iris advances the archive case.",
        continuity_update={},
    )


def _publication_run() -> SimpleNamespace:
    chapters = [_chapter(index) for index in range(1, 5)]
    total = sum(len(chapter.content.split()) for chapter in chapters)
    return SimpleNamespace(
        quality_profile="publication",
        chapters=chapters,
        target_word_count=total,
        min_words_per_chapter=1800,
        max_words_per_chapter=3200,
        story_bible={
            "ending_promise": "Expose the archive conspiracy and decide whether trust with Tarin can be rebuilt."
        },
        continuity_ledger={
            "open_threads": ["Iris and Tarin still have not rebuilt trust after the archive exposure."],
            "open_promises_by_name": {
                "public_exposure": "Expose the archive conspiracy publicly to the districts."
            },
            "trust_fractures": {"Iris/Tarin": "Their trust remains broken."},
            "emotional_open_loops": {},
            "memory_damage": {},
            "civilian_pressure_points": [],
        },
    )


def test_public_exposure_resolution_does_not_clear_distinct_trust_debt() -> None:
    run = _publication_run()
    qa = ManuscriptQaReport(
        ending_coherence_notes=[
            "The archive conspiracy public exposure is resolved on page when every district receives the evidence."
        ]
    )

    blockers = compile_publication_blockers(run, qa)

    assert any("1 of 2" in blocker and "candidate-specific" in blocker for blocker in blockers)


def test_empty_final_edit_is_rolled_back_even_when_stored_word_count_is_stale() -> None:
    session = _Session()
    pipeline = _Pipeline()
    run = _publication_run()
    chapter = run.chapters[0]
    old_content = chapter.content
    old_words = len(old_content.split())
    chapter.content = "   "
    chapter.word_count = old_words

    rolled_back = guard_final_edit_regressions(
        session,
        run,
        [chapter],
        {1: (old_content, old_words)},
        pipeline_module=pipeline,
    )

    assert rolled_back == [1]
    assert chapter.content == old_content
    assert chapter.word_count == old_words
    assert pipeline.events[-1][0] == "final_chapter_edit_guard_rollback"
    assert "final edit produced empty prose" in pipeline.events[-1][1]["reasons"]


def test_publication_word_count_uses_final_prose_not_stale_metadata() -> None:
    run = _publication_run()
    run.continuity_ledger = {
        "open_threads": [],
        "open_promises_by_name": {},
        "trust_fractures": {},
        "emotional_open_loops": {},
        "memory_damage": {},
        "civilian_pressure_points": [],
    }
    chapter = run.chapters[0]
    chapter.content = ""
    chapter.word_count = 2200

    blockers = compile_publication_blockers(run, ManuscriptQaReport())

    assert any("under-length" in blocker for blocker in blockers)
    assert any("materially under target" in blocker for blocker in blockers)
