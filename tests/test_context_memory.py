from __future__ import annotations

import json
from types import SimpleNamespace

from novel_generator.services.context_memory import compile_memory_packet
from novel_generator.services.context_runtime import _wrap_builder


def _large_ledger() -> dict:
    return {
        "current_patch_status": "The patch is public and cannot be recalled.",
        "world_state": "The city is under curfew after the archive breach.",
        "genre_state": {"recent_chapter_modes": "Chapter 30: investigation; Chapter 31: aftermath"},
        "character_states": {
            **{f"Citizen {index}": f"Peripheral state {index}." for index in range(30)},
            "Iris": "Exhausted, committed to exposing the vault, and unwilling to trust Tarin blindly.",
            "Tarin": "Ashamed of the betrayal and trying to recover Iris's trust.",
        },
        "open_threads": [
            *(f"Peripheral unresolved thread {index}." for index in range(35)),
            "Iris still needs proof that Tarin did not sell the vault map willingly.",
        ],
        "resolved_threads": [f"Resolved background thread {index}." for index in range(30)],
        "timeline": [
            *(f"Chapter {index}: background event {index}." for index in range(1, 61)),
            "Chapter 61: Tarin admits the vault map was coerced from him.",
        ],
        "active_entities": [
            *({"name": f"Entity {index}", "kind": "location", "role": "background"} for index in range(25)),
            {"name": "Black Vault", "kind": "location", "role": "source of the archive evidence"},
        ],
        "entity_state_changes": {
            **{f"Entity {index}": f"Changed in background event {index}." for index in range(25)},
            "Black Vault": "Its sealed archive is now physically exposed to the public inquiry.",
        },
        "open_promises_by_name": {
            **{f"promise_{index}": f"Background promise {index}." for index in range(25)},
            "tarin_betrayal": "Resolve whether Tarin betrayed Iris voluntarily or under coercion.",
        },
        "ideology_state_by_character": {
            "Iris": "Believes institutions deserve proof before trust.",
            "Tarin": "Believes survival sometimes justifies secrecy.",
        },
        "memory_damage": {},
        "trust_fractures": {"Iris/Tarin": "Trust fractured when the vault map reached the Directorate."},
        "civilian_pressure_points": [f"Civilian consequence {index}." for index in range(20)],
        "emotional_open_loops": {"Iris": "Has not decided whether Tarin deserves forgiveness."},
        "side_character_decisions": {
            "Tarin": ["Confessed the coercion publicly instead of fleeing."],
        },
        "system_state_by_name": {
            "Archive Lock": "Disabled after the Black Vault breach.",
        },
        "system_state_transitions": [
            {
                "system_name": "Archive Lock",
                "previous_state": "sealed",
                "new_state": "disabled",
                "cause": "Iris published the physical bypass proof.",
                "chapter_number": 61,
            }
        ],
    }


def test_small_ledger_passes_through_unchanged() -> None:
    ledger = {
        "current_patch_status": "No patch yet.",
        "world_state": "Opening equilibrium.",
        "open_threads": ["Find the missing witness."],
    }

    packet = compile_memory_packet(ledger, focus="missing witness", max_chars=4_000)

    assert packet.compacted is False
    assert packet.payload is ledger
    assert packet.output_chars == packet.source_chars


def test_large_ledger_is_bounded_and_keeps_relevant_continuity() -> None:
    ledger = _large_ledger()

    packet = compile_memory_packet(
        ledger,
        focus="Iris confronts Tarin about the Black Vault betrayal and whether she can trust him again.",
        max_chars=4_000,
    )

    assert packet.compacted is True
    assert packet.output_chars <= 4_000
    assert packet.payload["_memory_packet"]["compacted"] is True
    assert "Iris" in packet.payload.get("character_states", {})
    assert "Iris/Tarin" in packet.payload.get("trust_fractures", {})
    assert "tarin_betrayal" in packet.payload.get("open_promises_by_name", {})
    assert any("Tarin" in thread for thread in packet.payload.get("open_threads", []))
    assert ledger["timeline"][-1] == "Chapter 61: Tarin admits the vault map was coerced from him."


def test_prompt_wrapper_replaces_only_the_read_view() -> None:
    ledger = _large_ledger()
    original_snapshot = json.loads(json.dumps(ledger))

    def builder(continuity_ledger: dict, outline_entry: dict, chapter: object) -> list[dict[str, str]]:
        return [
            {
                "role": "user",
                "content": "Continuity ledger:\n" + json.dumps(continuity_ledger, indent=2),
            }
        ]

    wrapped = _wrap_builder(builder, budget_chars=4_000, horizon_lookahead=3)
    messages = wrapped(
        ledger,
        {"objective": "Iris confronts Tarin over the Black Vault betrayal."},
        SimpleNamespace(chapter_number=62, title="The Price of Trust", outline_summary="Iris demands the truth."),
    )

    assert "\"_memory_packet\"" in messages[0]["content"]
    assert len(messages[0]["content"]) < len("Continuity ledger:\n" + json.dumps(ledger, indent=2))
    assert ledger == original_snapshot
