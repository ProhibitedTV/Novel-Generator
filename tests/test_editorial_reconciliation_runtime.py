from __future__ import annotations

from types import SimpleNamespace

from novel_generator.schemas import ChapterContinuityUpdate, ContinuityLedger
from novel_generator.services.editorial_reconciliation_runtime import (
    _wrap_developmental_revision_waves,
    pending_reconciliation_chapters,
    reconcile_developmental_continuity,
)


def _event(sequence: int, event_type: str, chapter_number: int) -> SimpleNamespace:
    return SimpleNamespace(
        sequence=sequence,
        event_type=event_type,
        payload={"chapter_number": chapter_number},
    )


def _continuity(number: int, *, thread: str = "") -> dict:
    return ChapterContinuityUpdate(
        chapter_outcome=f"Outcome {number}",
        current_patch_status=f"Patch {number}",
        character_states={"Iris": f"State {number}"},
        world_state=f"World {number}",
        open_threads=[thread] if thread else [],
        resolved_threads=[],
        timeline_entry=f"Timeline {number}",
        timeline=[f"Timeline {number}"],
    ).model_dump()


def test_pending_reconciliation_uses_latest_revision_and_reconcile_sequences() -> None:
    run = SimpleNamespace(
        events=[
            _event(1, "developmental_chapter_revision_completed", 2),
            _event(2, "developmental_continuity_reconciled", 2),
            _event(3, "developmental_chapter_revision_completed", 4),
            _event(4, "developmental_continuity_reconciled", 5),
            _event(5, "developmental_chapter_revision_completed", 5),
            _event(6, "developmental_continuity_reconciled", 5),
            _event(7, "developmental_chapter_revision_completed", 2),
        ]
    )

    assert pending_reconciliation_chapters(run) == [2, 4]


def test_reconciliation_refreshes_only_revised_chapter_then_replays_ledger() -> None:
    chapter1 = SimpleNamespace(
        chapter_number=1,
        summary="old summary 1",
        continuity_update=_continuity(1, thread="old thread"),
    )
    chapter2 = SimpleNamespace(
        chapter_number=2,
        summary="saved summary 2",
        continuity_update=_continuity(2),
    )
    run = SimpleNamespace(
        project=SimpleNamespace(title="Test"),
        events=[_event(1, "developmental_chapter_revision_completed", 1)],
        continuity_ledger={},
    )
    calls = {"summary": 0, "continuity": 0}

    def record_event(session, target_run, event_type, payload):
        sequence = max([getattr(event, "sequence", 0) for event in target_run.events] + [0]) + 1
        target_run.events.append(SimpleNamespace(sequence=sequence, event_type=event_type, payload=payload))

    def merge(ledger: ContinuityLedger, update: ChapterContinuityUpdate) -> ContinuityLedger:
        return ledger.model_copy(
            update={
                "current_patch_status": update.current_patch_status,
                "world_state": update.world_state,
                "character_states": dict(update.character_states),
                "open_threads": list(update.open_threads),
                "timeline": [*ledger.timeline, update.timeline_entry],
            }
        )

    refreshed = ChapterContinuityUpdate(
        chapter_outcome="Revised outcome 1",
        current_patch_status="Revised patch 1",
        character_states={"Iris": "Revised state 1"},
        world_state="Revised world 1",
        open_threads=["new revised thread"],
        resolved_threads=[],
        timeline_entry="Revised timeline 1",
        timeline=["Revised timeline 1"],
    )

    fake_pipeline = SimpleNamespace(
        _story_bible_from_run=lambda target_run: {},
        _build_initial_ledger=lambda story_bible: ContinuityLedger(
            current_patch_status="initial",
            world_state="initial",
        ),
        _outline_entry=lambda target_run, number: SimpleNamespace(chapter_mode="aftermath"),
        _resolve_stage_route=lambda client, target_run, stage: ("ollama", "model"),
        build_summary_messages=lambda chapter, outline: [],
        _supervised_provider_chat=lambda *args, **kwargs: (
            calls.__setitem__("summary", calls["summary"] + 1) or "refreshed summary 1"
        ),
        build_continuity_update_messages=lambda *args, **kwargs: [],
        parse_continuity_update=lambda text: refreshed,
        _generate_structured_output=lambda *args, **kwargs: (
            calls.__setitem__("continuity", calls["continuity"] + 1) or refreshed.model_copy(deep=True)
        ),
        _ledger_from_update=merge,
        _ledger_with_chapter_mode=lambda ledger, number, mode: ledger,
        _apply_continuity_canon_warnings=lambda chapter, update: None,
        record_event=record_event,
    )
    session = SimpleNamespace(commits=0)
    session.commit = lambda: setattr(session, "commits", session.commits + 1)

    completed = reconcile_developmental_continuity(
        session,
        run,
        [chapter1, chapter2],
        object(),
        pipeline_module=fake_pipeline,
    )

    assert completed == [1]
    assert calls == {"summary": 1, "continuity": 1}
    assert chapter1.summary == "refreshed summary 1"
    assert chapter1.continuity_update["chapter_outcome"] == "Revised outcome 1"
    assert chapter2.summary == "saved summary 2"
    assert run.continuity_ledger["world_state"] == "World 2"
    assert any(event.event_type == "developmental_continuity_reconciled" for event in run.events)
    assert pending_reconciliation_chapters(run) == []


def test_developmental_wrapper_reconciles_after_revision_wave() -> None:
    order: list[str] = []

    def waves(session, run, chapters, qa_report, developmental_plan, client):
        order.append("waves")

    def reconcile(session, run, chapters, client):
        order.append("reconcile")
        return [1]

    wrapped = _wrap_developmental_revision_waves(waves, reconcile=reconcile)
    wrapped(object(), object(), [object()], object(), object(), object())

    assert order == ["waves", "reconcile"]
