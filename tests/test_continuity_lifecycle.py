from __future__ import annotations

from novel_generator.schemas import ChapterContinuityUpdate, ContinuityLedger
from novel_generator.services.continuity_lifecycle import apply_continuity_lifecycle


def _merged_ledger() -> ContinuityLedger:
    return ContinuityLedger(
        current_patch_status="Patch remains public.",
        character_states={"Iris": "Committed to the inquiry."},
        world_state="The inquiry is underway.",
        open_threads=[
            "Find out who hid the obsidian witness.",
            "The district still needs a safe evidence custodian.",
            "Tarin must decide whether to testify.",
        ],
        resolved_threads=["Find out who hid the obsidian witness."],
        timeline=["Chapter 8: the witness was recovered."],
        open_promises_by_name={
            "witness": "Explain who hid the witness.",
            "testimony": "Will Tarin testify?",
        },
        memory_damage={"Iris": "Still missing one hour of memory."},
        trust_fractures={"Iris/Tarin": "Trust remains damaged."},
        civilian_pressure_points=["Ration access remains unstable.", "The old evacuation shelter is overcrowded."],
        emotional_open_loops={"Iris": "Has not forgiven Tarin."},
    )


def _update() -> ChapterContinuityUpdate:
    return ChapterContinuityUpdate(
        chapter_outcome="The witness mystery is resolved and Iris forgives Tarin.",
        current_patch_status="Patch remains public.",
        character_states={"Iris": "Committed to the inquiry."},
        world_state="The inquiry is underway.",
        open_threads=[
            "Find out who hid the obsidian witness.",
            "The district still needs a safe evidence custodian.",
            "Tarin must decide whether to testify.",
        ],
        resolved_threads=["Find out who hid the obsidian witness."],
        timeline_entry="Chapter 9: the witness mystery was resolved.",
        open_promises_by_name={"testimony": "Will Tarin testify?"},
        memory_damage={},
        trust_fractures={},
        civilian_pressure_points=["Ration access remains unstable."],
        emotional_open_loops={},
    )


def test_live_continuity_fields_use_post_chapter_snapshot_semantics() -> None:
    cleaned = apply_continuity_lifecycle(_merged_ledger(), _update())

    assert "Find out who hid the obsidian witness." not in cleaned.open_threads
    assert cleaned.open_threads == [
        "The district still needs a safe evidence custodian.",
        "Tarin must decide whether to testify.",
    ]
    assert cleaned.open_promises_by_name == {"testimony": "Will Tarin testify?"}
    assert cleaned.memory_damage == {}
    assert cleaned.trust_fractures == {}
    assert cleaned.emotional_open_loops == {}
    assert cleaned.civilian_pressure_points == ["Ration access remains unstable."]
    assert "Find out who hid the obsidian witness." in cleaned.resolved_threads
    assert cleaned.timeline == ["Chapter 8: the witness was recovered."]


def test_rephrased_resolved_thread_can_close_substantive_open_thread() -> None:
    merged = _merged_ledger().model_copy(
        update={
            "open_threads": ["Determine whether the Directorate deliberately sabotaged the archive pressure seals."],
            "resolved_threads": ["The Directorate deliberately sabotaged the archive pressure seals."],
        }
    )
    update = _update().model_copy(
        update={
            "open_threads": ["Determine whether the Directorate deliberately sabotaged the archive pressure seals."],
            "resolved_threads": ["The Directorate deliberately sabotaged the archive pressure seals."],
        }
    )

    cleaned = apply_continuity_lifecycle(merged, update)

    assert cleaned.open_threads == []


def test_omitted_snapshot_fields_preserve_live_debt_across_checkpoint_replay() -> None:
    update = ChapterContinuityUpdate(
        chapter_outcome="The inquiry continues.", current_patch_status="Public.",
        world_state="The inquiry is underway.", timeline_entry="Chapter 10: the inquiry continues.",
    )
    checkpoint = update.model_dump(exclude_unset=True)
    replayed = ChapterContinuityUpdate.model_validate(checkpoint)
    merged = _merged_ledger()
    cleaned = apply_continuity_lifecycle(merged, replayed)

    assert cleaned.open_promises_by_name == merged.open_promises_by_name
    assert cleaned.memory_damage == merged.memory_damage
    assert cleaned.trust_fractures == merged.trust_fractures
    assert cleaned.emotional_open_loops == merged.emotional_open_loops
    assert cleaned.civilian_pressure_points == merged.civilian_pressure_points
    assert cleaned.open_threads == merged.open_threads[1:]


def test_explicit_resolution_does_not_clear_other_omitted_snapshot_fields() -> None:
    update = ChapterContinuityUpdate(
        chapter_outcome="Iris forgives Tarin.", current_patch_status="Public.",
        world_state="The inquiry continues.", timeline_entry="Chapter 10: forgiveness.",
        emotional_open_loops={},
    )
    cleaned = apply_continuity_lifecycle(_merged_ledger(), update)
    assert cleaned.emotional_open_loops == {}
    assert cleaned.open_promises_by_name == _merged_ledger().open_promises_by_name
