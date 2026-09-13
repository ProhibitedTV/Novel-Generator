from __future__ import annotations

from types import SimpleNamespace

from novel_generator.services.longform_runtime import _wrap_arc_builder
from novel_generator.services.story_arc_context import compile_story_arc_audit


def _chapter(number: int, summary: str) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        title=f"Chapter {number}",
        outline_summary=summary,
        summary=summary,
        plan="",
        continuity_update={"chapter_outcome": summary},
    )


def _run() -> SimpleNamespace:
    outline = [
        {
            "chapter_number": number,
            "title": f"Chapter {number}",
            "objective": f"Objective {number}",
            "reveal": "",
            "ending_state": f"State {number}",
        }
        for number in range(1, 13)
    ]
    outline[8]["objective"] = "The obsidian witness resurfaces and forces Iris to confront the old vault promise."
    outline[9]["objective"] = "Tarin returns with evidence that changes Iris's options."
    chapters = [
        _chapter(1, "Iris enters the archive district."),
        _chapter(2, "Tarin hides the obsidian witness after the vault breach."),
        _chapter(3, "Iris handles civic fallout with Mara."),
        _chapter(4, "Iris negotiates ration access with Mara."),
        _chapter(5, "Iris survives a public inquiry."),
        _chapter(6, "Mara makes an independent political bargain."),
        _chapter(7, "Iris faces the cost of the inquiry."),
    ]
    run = SimpleNamespace(
        requested_chapters=12,
        outline=outline,
        chapters=chapters,
        story_bible={
            "character_agendas": [
                {
                    "name": "Iris",
                    "want": "Expose the archive conspiracy.",
                    "fear": "Becoming another institution that hides truth.",
                    "line_in_sand": "Will not fabricate evidence.",
                    "public_belief": "Proof should precede trust.",
                    "private_pressure": "She fears isolation.",
                },
                {
                    "name": "Tarin",
                    "want": "Protect the obsidian witness from the Directorate.",
                    "fear": "His coercion will look like betrayal.",
                    "line_in_sand": "Will not surrender the witness voluntarily.",
                    "public_belief": "Survival can justify secrecy.",
                    "private_pressure": "He wants Iris to forgive him.",
                },
            ]
        },
        continuity_ledger={
            "character_states": {
                "Iris": "Under public scrutiny after the inquiry.",
                "Tarin": "Missing after hiding the obsidian witness.",
            },
            "ideology_state_by_character": {
                "Iris": "Testing whether proof can coexist with loyalty.",
                "Tarin": "Still believes secrecy can protect people.",
            },
            "emotional_open_loops": {"Tarin": "Needs to face Iris after the apparent betrayal."},
            "trust_fractures": {"Iris/Tarin": "Iris believes Tarin may have chosen the Directorate."},
            "side_character_decisions": {"Tarin": ["Hid the obsidian witness instead of surrendering it."]},
            "open_promises_by_name": {
                "vault_witness": "Reveal why the obsidian witness was hidden and whether Tarin betrayed Iris."
            },
            "open_threads": ["The obsidian witness is still hidden."],
        },
    )
    for chapter in chapters:
        chapter.run = run
    return run


def test_story_arc_audit_flags_dormant_unresolved_character_and_subplot() -> None:
    audit = compile_story_arc_audit(_run(), 8, max_chars=12_000, dormant_after=4).payload

    tarin = next(row for row in audit["character_arcs"] if row["name"] == "Tarin")
    assert tarin["last_touched_chapter"] == 2
    assert tarin["dormant_for_chapters"] == 5
    assert tarin["attention"] == "dormant_unresolved"
    assert 10 in tarin["upcoming_planned_touches"]

    promise = next(row for row in audit["subplot_lanes"] if row["type"] == "promise")
    assert promise["attention"] == "dormant_unresolved"
    assert 9 in promise["upcoming_planned_touches"]
    assert audit["_story_arc_audit"]["dormant_character_count"] >= 1
    assert audit["_story_arc_audit"]["dormant_subplot_count"] >= 1


def test_arc_wrapper_injects_read_only_audit_into_chapter_prompt() -> None:
    run = _run()
    chapter = SimpleNamespace(chapter_number=8, title="Chapter 8", run=run)

    def build_chapter_plan_messages(run: object, chapter: object) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Plan the chapter."},
            {"role": "user", "content": "Current chapter outline:\n{}"},
        ]

    wrapped = _wrap_arc_builder(build_chapter_plan_messages, max_chars=12_000, dormant_after=4)
    messages = wrapped(run, chapter)
    prompt = messages[-1]["content"]

    assert "Story arc audit (read-only long-form state" in prompt
    assert '"name":"Tarin"' in prompt
    assert '"attention":"dormant_unresolved"' in prompt
    assert "Current chapter outline:" in prompt
