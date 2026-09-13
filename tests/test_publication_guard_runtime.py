from __future__ import annotations

from types import SimpleNamespace

from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services.publication_guard_runtime import (
    _wrap_readiness_gate,
    compile_publication_blockers,
    guard_final_edit_regressions,
)


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _Pipeline:
    PUBLICATION_READINESS_THRESHOLD = 7

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    @staticmethod
    def _is_publication_run(run) -> bool:
        return str(getattr(run, "quality_profile", "")) == "publication"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))

    def record_event(self, session, run, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _chapter(number: int, words: int) -> SimpleNamespace:
    content = (f"chapter {number} concrete scene consequence " * max(1, words // 5)).strip()
    return SimpleNamespace(
        chapter_number=number,
        title=f"Chapter {number}",
        content=content,
        word_count=words,
        summary=f"Chapter {number} changes the story.",
        continuity_update={},
    )


def _run(*, words_each: int = 2500, target: int = 10_000) -> SimpleNamespace:
    chapters = [_chapter(number, words_each) for number in range(1, 5)]
    return SimpleNamespace(
        quality_profile="publication",
        chapters=chapters,
        target_word_count=target,
        min_words_per_chapter=1800,
        max_words_per_chapter=3200,
        story_bible={
            "ending_promise": "Expose the archive conspiracy and decide whether trust with Tarin can be rebuilt."
        },
        continuity_ledger={
            "open_threads": ["Tarin and Iris still have not rebuilt trust after exposing the archive conspiracy."],
            "open_promises_by_name": {},
            "trust_fractures": {"Iris/Tarin": "Their trust remains broken."},
            "emotional_open_loops": {},
            "memory_damage": {},
            "civilian_pressure_points": [],
        },
    )


def test_publication_blockers_detect_unclassified_central_ending_debt() -> None:
    run = _run()
    qa = ManuscriptQaReport(ending_coherence_notes=[])

    blockers = compile_publication_blockers(run, qa)

    assert any("ending promise" in blocker for blocker in blockers)


def test_publication_blockers_allow_explicit_intentional_aftermath_classification() -> None:
    run = _run()
    qa = ManuscriptQaReport(
        ending_coherence_notes=[
            "The archive conspiracy is paid off on page; the remaining Iris/Tarin trust fracture is intentional aftermath."
        ]
    )

    blockers = compile_publication_blockers(run, qa)

    assert not any("ending promise" in blocker for blocker in blockers)


def test_publication_blockers_detect_material_whole_book_length_miss() -> None:
    run = _run(words_each=1500, target=10_000)
    run.continuity_ledger = {
        "open_threads": [],
        "open_promises_by_name": {},
        "trust_fractures": {},
        "emotional_open_loops": {},
        "memory_damage": {},
        "civilian_pressure_points": [],
    }
    qa = ManuscriptQaReport()

    blockers = compile_publication_blockers(run, qa)

    assert any("materially under target" in blocker for blocker in blockers)
    assert any("under-length" in blocker for blocker in blockers)


def test_final_edit_guard_rolls_back_catastrophic_shrink() -> None:
    pipeline = _Pipeline()
    session = _Session()
    run = _run(words_each=2200, target=8800)
    chapter = run.chapters[0]
    old_content = chapter.content
    before = {1: (old_content, 2200)}
    chapter.content = "abrupt ending " * 450
    chapter.word_count = 900

    rolled_back = guard_final_edit_regressions(
        session,
        run,
        [chapter],
        before,
        pipeline_module=pipeline,
    )

    assert rolled_back == [1]
    assert chapter.content == old_content
    assert chapter.word_count == 2200
    assert session.commits == 1
    assert pipeline.events[-1][0] == "final_chapter_edit_guard_rollback"


def test_final_edit_guard_allows_normal_line_edit_delta() -> None:
    pipeline = _Pipeline()
    session = _Session()
    run = _run(words_each=2200, target=8800)
    chapter = run.chapters[0]
    before = {1: (chapter.content, 2200)}
    chapter.content = "polished concrete scene consequence " * 400
    chapter.word_count = 2000

    rolled_back = guard_final_edit_regressions(
        session,
        run,
        [chapter],
        before,
        pipeline_module=pipeline,
    )

    assert rolled_back == []
    assert chapter.word_count == 2000
    assert session.commits == 0
    assert pipeline.events == []


def test_readiness_wrapper_downgrades_false_publication_ready_result() -> None:
    pipeline = _Pipeline()
    session = _Session()
    run = _run(words_each=1500, target=10_000)

    def base_gate(session, run, qa_report):
        return qa_report.model_copy(
            update={
                "publication_readiness_scores": {"publication_readiness": 8},
                "publication_readiness_label": "publication-ready",
                "publication_readiness_summary": "Passed.",
            }
        )

    guarded = _wrap_readiness_gate(base_gate, pipeline_module=pipeline)
    result = guarded(session, run, ManuscriptQaReport())

    assert result.publication_readiness_label == "needs editorial revision"
    assert result.publication_readiness_scores["publication_readiness"] == 6
    assert any(item.startswith("Publication blocker:") for item in result.warnings)
    assert pipeline.events[-1][0] == "publication_readiness_guard_applied"
    assert session.commits == 1
