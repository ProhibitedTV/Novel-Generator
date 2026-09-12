from __future__ import annotations

import json
from types import SimpleNamespace

from novel_generator.services.context_runtime import _wrap_builder
from novel_generator.services.narrative_horizon import compile_narrative_horizon


def _outline_row(number: int, mode: str) -> dict:
    return {
        "chapter_number": number,
        "act": "Act I" if number <= 2 else "Act II",
        "title": f"Chapter {number}",
        "objective": f"Objective {number}",
        "conflict_turn": f"Conflict {number}",
        "character_turn": f"Character turn {number}",
        "reveal": f"Reveal {number}",
        "ending_state": f"Ending state {number}",
        "outcome_type": "setback" if number % 2 else "compromise",
        "primary_obstacle": f"Obstacle {number}",
        "cost_if_success": f"Cost {number}",
        "chapter_mode": mode,
        "independent_side_character_move": f"Side move {number}",
        "concrete_ending_hook": {
            "trigger": f"Hook {number}",
            "visible_object_or_actor": f"Actor {number}",
            "next_problem": f"Problem {number + 1}",
        },
    }


def _completed_chapter(number: int, mode: str, word_count: int) -> SimpleNamespace:
    plan = {
        "chapter_mode": mode,
        "conflict_turn": f"Planned conflict {number}",
        "emotional_anchor": f"Emotion {number}",
        "independent_side_character_move": f"Independent move {number}",
    }
    continuity = {
        "chapter_outcome": f"Outcome {number}",
        "story_turn": {
            "irreversible_change": f"Irreversible change {number}",
            "protagonist_choice": f"Choice {number}",
            "permanent_consequence": f"Permanent consequence {number}",
            "state_after": f"State after {number}",
        },
    }
    return SimpleNamespace(
        chapter_number=number,
        title=f"Chapter {number}",
        plan=json.dumps(plan),
        content=f"Draft prose {number}",
        summary=f"Summary {number}",
        continuity_update=continuity,
        word_count=word_count,
    )


def _run() -> SimpleNamespace:
    modes = [
        "investigation",
        "interpersonal_confrontation",
        "aftermath",
        "moral_negotiation",
        "physical_escape",
        "civic_fallout",
    ]
    return SimpleNamespace(
        target_word_count=12_000,
        requested_chapters=6,
        min_words_per_chapter=1_500,
        max_words_per_chapter=2_500,
        outline=[_outline_row(index, modes[index - 1]) for index in range(1, 7)],
        chapters=[
            _completed_chapter(1, modes[0], 1_500),
            _completed_chapter(2, modes[1], 1_600),
            _completed_chapter(3, modes[2], 1_700),
            SimpleNamespace(
                chapter_number=4,
                title="Chapter 4",
                plan=None,
                content=None,
                summary=None,
                continuity_update=None,
                word_count=0,
            ),
        ],
        story_bible={},
        continuity_ledger={},
    )


def test_narrative_horizon_carries_previous_consequence_future_commitments_and_word_budget() -> None:
    horizon = compile_narrative_horizon(_run(), 4, lookahead=2).payload

    assert horizon["_narrative_horizon"]["chapter_number"] == 4
    assert horizon["_narrative_horizon"]["book_progress_percent"] == 66.7
    assert horizon["causal_inheritance"]["state_after"] == "State after 3"
    assert horizon["causal_inheritance"]["permanent_consequence"] == "Permanent consequence 3"
    assert [item["chapter_number"] for item in horizon["upcoming_commitments"]] == [5, 6]
    assert horizon["upcoming_commitments"][0]["reveal"] == "Reveal 5"
    assert horizon["recent_pattern_history"][-1]["chapter_mode"] == "aftermath"
    assert horizon["word_budget"]["completed_words_before_this_chapter"] == 4_800
    assert horizon["word_budget"]["required_average_from_here"] == 2_400
    assert horizon["word_budget"]["adaptive_target_this_chapter"] == 2_400
    assert horizon["word_budget"]["pace_status"] == "behind"
    assert horizon["word_budget"]["target_reachable_with_configured_max"] is True
    assert horizon["ending_convergence"] == {}
    assert any("precondition" in rule for rule in horizon["causal_rules"])
    assert any("adaptive_target_this_chapter" in rule for rule in horizon["causal_rules"])


def test_late_book_horizon_spends_open_story_debt_instead_of_multiplying_it() -> None:
    run = _run()
    run.story_bible = {
        "ending_promise": "Iris exposes the archive conspiracy and decides whether Tarin can be trusted again."
    }
    run.continuity_ledger = {
        "open_promises_by_name": {
            "vault_witness": "Resolve why the witness was hidden.",
            "tarin_betrayal": "Resolve whether Tarin betrayed Iris voluntarily.",
        },
        "open_threads": ["The archive evidence still has no safe public custodian."],
        "emotional_open_loops": {"Iris": "She has not decided whether Tarin deserves forgiveness."},
        "trust_fractures": {"Iris/Tarin": "The apparent betrayal remains unresolved."},
    }

    convergence = compile_narrative_horizon(run, 5, lookahead=1).payload["ending_convergence"]

    # With only two chapters remaining, closure pressure overrides the percentage-only phase.
    assert convergence["phase"] == "resolution_priority"
    assert convergence["remaining_chapters_including_this_chapter"] == 2
    assert convergence["new_major_threads_allowed"] == 0
    assert convergence["open_promise_count"] == 2
    assert "tarin_betrayal" in convergence["priority_open_promises"]
    assert "archive conspiracy" in convergence["ending_promise"]
    assert any("new major faction" in rule for rule in convergence["rules"])
    assert any("sequel hook" in rule.lower() for rule in convergence["rules"])

    final_convergence = compile_narrative_horizon(run, 6, lookahead=0).payload["ending_convergence"]
    assert final_convergence["phase"] == "resolution_priority"
    assert any("sequel hook" in rule.lower() for rule in final_convergence["rules"])


def test_chapter_prompt_wrapper_injects_narrative_horizon_even_when_ledger_is_small() -> None:
    run = _run()
    chapter = run.chapters[-1]

    def build_chapter_plan_messages(
        project: object,
        run: object,
        chapter: object,
        outline_entry: dict,
        story_bible: dict,
        continuity_ledger: dict,
        prior_context: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Plan a chapter."},
            {
                "role": "user",
                "content": "Continuity ledger:\n"
                + json.dumps(continuity_ledger, indent=2)
                + "\n\nCurrent chapter outline:\n"
                + json.dumps(outline_entry, indent=2),
            },
        ]

    wrapped = _wrap_builder(build_chapter_plan_messages, budget_chars=4_000, horizon_lookahead=2)
    messages = wrapped(
        object(),
        run,
        chapter,
        run.outline[3],
        {},
        {"world_state": "Stable", "open_threads": ["Thread"]},
        "Summary 3",
    )

    prompt = messages[-1]["content"]
    assert "Narrative horizon (causal contract" in prompt
    assert '"chapter_number":4' in prompt
    assert '"chapter_number":5' in prompt
    assert '"state_after":"State after 3"' in prompt
    assert '"adaptive_target_this_chapter":2400' in prompt
