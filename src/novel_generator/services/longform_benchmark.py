from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from .chapter_recall import compile_chapter_recall
from .context_memory import compile_memory_packet
from .manuscript_context import compile_manuscript_capsules
from .manuscript_qa_context import compile_manuscript_qa_capsules
from .narrative_horizon import compile_narrative_horizon
from .story_arc_context import compile_story_arc_audit


def _outline_row(number: int, total: int) -> dict[str, Any]:
    objective = f"Advance archive investigation beat {number}."
    reveal = f"Archive reveal {number}."
    character_turn = f"Iris changes strategy after consequence {number}."
    if number == 23:
        objective = "Tarin returns with evidence that reframes the old betrayal."
        character_turn = "Tarin makes an independent choice that changes Iris's options."
    if number == 24:
        objective = "Use the obsidian witness key to reopen the sealed testimony without replaying its discovery."
        reveal = "The obsidian witness key authenticates the hidden testimony."
    return {
        "chapter_number": number,
        "act": "Act I" if number <= total // 4 else ("Act III" if number > total * 3 // 4 else "Act II"),
        "title": f"Archive Chapter {number}",
        "objective": objective,
        "conflict_turn": f"The archive pushes back in a distinct way {number}.",
        "character_turn": character_turn,
        "reveal": reveal,
        "ending_state": f"Story state {number} becomes irreversible.",
        "outcome_type": "setback" if number % 3 == 0 else "compromise",
        "primary_obstacle": f"Obstacle class {number % 7}",
        "cost_if_success": f"Cost {number} becomes unavoidable.",
        "chapter_mode": [
            "investigation",
            "interpersonal_confrontation",
            "aftermath",
            "moral_negotiation",
            "physical_escape",
            "civic_fallout",
        ][number % 6],
        "independent_side_character_move": f"A supporting character changes leverage {number}.",
        "concrete_ending_hook": {
            "trigger": f"Visible trigger {number}",
            "visible_object_or_actor": f"Actor {number}",
            "next_problem": f"Problem {min(total, number + 1)}",
        },
    }


def _chapter(number: int, *, word_count: int = 2200) -> SimpleNamespace:
    summary = f"Iris completes archive step {number} and accepts consequence {number}."
    outline_summary = f"Archive investigation beat {number}; irreversible state {number}."
    plan: dict[str, Any] = {
        "chapter_mode": "investigation" if number % 2 else "aftermath",
        "conflict_turn": f"Plan conflict {number}",
        "emotional_anchor": f"Iris emotional beat {number}",
        "independent_side_character_move": f"Independent move {number}",
    }
    character_states: dict[str, str] = {"Iris": f"Iris state after chapter {number}."}

    if number == 4:
        summary = (
            "Iris discovers the obsidian witness key inside a false maintenance panel, but cannot yet identify "
            "which sealed testimony it authenticates. She hides the key instead of surrendering it."
        )
        outline_summary = "The obsidian witness key becomes a durable unresolved callback object."
    if number == 5:
        summary = (
            "Tarin refuses Iris's demand for disclosure and vanishes after choosing to protect a source, leaving "
            "their apparent betrayal unresolved."
        )
        character_states["Tarin"] = "Tarin is absent after an unresolved apparent betrayal."
        plan["emotional_anchor"] = "Iris and Tarin fracture their trust."

    continuity = {
        "chapter_outcome": f"Outcome {number}",
        "character_states": character_states,
        "story_turn": {
            "irreversible_change": f"Archive consequence {number} cannot be undone.",
            "protagonist_choice": f"Iris chooses route {number}.",
            "permanent_consequence": f"Permanent cost {number} remains in force.",
            "state_after": f"State after chapter {number}.",
        },
        "open_promises_by_name": {
            "ending_promise": "Expose the archive conspiracy and settle whether trust can be rebuilt."
        },
    }
    qa = {
        "forward_motion_score": 8,
        "repetition_risk_score": 8,
        "emotional_depth_score": 8,
        "warnings": [] if number % 8 else [f"Synthetic warning {number}"],
        "focus": [f"Preserve consequence {number}"],
        "revision_required": False,
    }
    content = (f"Chapter {number} scene prose with concrete action and consequence. " * 90).strip()
    return SimpleNamespace(
        chapter_number=number,
        title=f"Archive Chapter {number}",
        outline_summary=outline_summary,
        plan=json.dumps(plan),
        summary=summary,
        continuity_update=continuity,
        qa_notes=qa,
        word_count=word_count,
        content=content,
    )


def build_synthetic_longform_run(chapter_count: int = 32) -> SimpleNamespace:
    total = max(12, int(chapter_count))
    chapters = [_chapter(number) for number in range(1, total)]
    outline = [_outline_row(number, total) for number in range(1, total + 1)]

    timeline = [
        f"Historical archive event {index}: district {index % 11} changes custody after evidence pressure {index}."
        for index in range(1, 141)
    ]
    active_entities = [
        {
            "name": f"Archive Entity {index}",
            "kind": "faction" if index % 2 else "location",
            "role": f"Recurring synthetic entity with consequence chain {index}." * 3,
        }
        for index in range(1, 45)
    ]
    open_threads = [
        "The obsidian witness key still needs to authenticate the sealed testimony.",
        "The public archive evidence still lacks a safe custodian.",
        "Iris must decide whether Tarin's apparent betrayal was voluntary.",
    ]
    ledger = {
        "current_patch_status": "The archive cannot return to its opening equilibrium.",
        "world_state": "Public confidence is collapsing while the archive conspiracy becomes visible.",
        "open_threads": open_threads,
        "resolved_threads": [f"Resolved historical thread {index}" for index in range(1, 35)],
        "timeline": timeline,
        "active_entities": active_entities,
        "entity_state_changes": {f"Entity {index}": f"Changed state {index}." for index in range(1, 40)},
        "open_promises_by_name": {
            "obsidian_witness": "The obsidian witness key must authenticate the sealed testimony.",
            "tarin_betrayal": "Determine whether Tarin betrayed Iris voluntarily.",
            "ending_promise": "Expose the archive conspiracy and decide whether trust can be rebuilt.",
        },
        "character_states": {
            "Iris": "Iris has public evidence but cannot safely release it.",
            "Tarin": "Tarin remains absent after an unresolved apparent betrayal.",
        },
        "ideology_state_by_character": {"Iris": "Truth needs accountable custody.", "Tarin": "Protect sources before institutions."},
        "memory_damage": {},
        "trust_fractures": {"Iris/Tarin": "The apparent betrayal remains unresolved."},
        "civilian_pressure_points": [f"District {index} faces archive fallout." for index in range(1, 18)],
        "emotional_open_loops": {"Tarin": "Tarin has not explained why he chose the source over Iris."},
        "side_character_decisions": {"Tarin": ["Tarin protected a source and disappeared."]},
        "genre_state": {"pressure": "high", "public_exposure": "accelerating"},
        "system_state_by_name": {f"Archive Node {index}": f"State {index}" for index in range(1, 22)},
        "system_state_transitions": [
            {"system_name": f"Archive Node {index % 9}", "new_state": f"Transition {index}"}
            for index in range(1, 55)
        ],
    }
    story_bible = {
        "ending_promise": "Iris exposes the archive conspiracy and makes a final trust decision about Tarin.",
        "character_agendas": [
            {
                "name": "Iris",
                "want": "Expose the archive conspiracy without sacrificing civilians.",
                "fear": "Becoming another unaccountable custodian of truth.",
                "line_in_sand": "She will not erase inconvenient testimony.",
                "public_belief": "Evidence should survive institutions.",
                "private_pressure": "She fears needing Tarin again.",
            },
            {
                "name": "Tarin",
                "want": "Keep vulnerable sources alive long enough for the truth to matter.",
                "fear": "Iris will expose a source to win publicly.",
                "line_in_sand": "He will not trade a source for institutional legitimacy.",
                "public_belief": "Protection comes before disclosure.",
                "private_pressure": "He has not explained the apparent betrayal.",
            },
        ],
    }
    return SimpleNamespace(
        requested_chapters=total,
        target_word_count=80_000,
        min_words_per_chapter=1_800,
        max_words_per_chapter=3_200,
        outline=outline,
        chapters=chapters,
        story_bible=story_bible,
        continuity_ledger=ledger,
    )


def run_longform_benchmark(chapter_count: int = 32) -> dict[str, Any]:
    """Run a deterministic stress suite over the long-form context/state architecture.

    This intentionally uses no model call. It answers a narrower but important question before live
    generation: when a book becomes large and messy, do the deterministic systems still retrieve,
    bound, prioritize, and converge the correct story state?
    """

    run = build_synthetic_longform_run(chapter_count)
    total = run.requested_chapters
    callback_chapter = min(24, total - 3)
    late_chapter = max(2, total - 1)

    memory = compile_memory_packet(
        run.continuity_ledger,
        focus={"objective": "obsidian witness key Tarin betrayal archive testimony"},
        max_chars=6_000,
    )
    recall = compile_chapter_recall(
        run,
        callback_chapter,
        focus=run.outline[callback_chapter - 1],
        max_chars=6_000,
        recent_chapters=2,
        relevant_chapters=4,
    )
    arc = compile_story_arc_audit(
        run,
        min(20, total - 4),
        max_chars=10_000,
        dormant_after=5,
    ).payload
    horizon = compile_narrative_horizon(run, callback_chapter, lookahead=3).payload
    ending = compile_narrative_horizon(run, late_chapter, lookahead=1).payload
    manuscript = compile_manuscript_capsules(run.chapters, max_chars=30_000)
    manuscript_qa = compile_manuscript_qa_capsules(run.chapters, max_chars=30_000)

    recalled_rows = recall.payload.get("chapters", [])
    callback_found = any(
        row.get("chapter_number") == 4 and row.get("selection") == "relevant_callback"
        for row in recalled_rows
    )
    tarin_rows = [row for row in arc.get("character_arcs", []) if row.get("name") == "Tarin"]
    tarin = tarin_rows[0] if tarin_rows else {}
    pacing = horizon.get("word_budget", {})
    convergence = ending.get("ending_convergence", {})

    checks = {
        "continuity_memory_compacts": bool(memory.compacted and memory.output_chars <= 6_000),
        "buried_callback_recalled": callback_found,
        "dormant_character_detected": tarin.get("attention") == "dormant_unresolved",
        "future_character_touch_visible": bool(tarin.get("upcoming_planned_touches")),
        "behind_pace_detected": pacing.get("pace_status") == "behind" and pacing.get("adaptive_target_this_chapter", 0) > run.min_words_per_chapter,
        "late_book_resolution_priority": convergence.get("phase") == "resolution_priority" and convergence.get("new_major_threads_allowed") == 0,
        "developmental_map_keeps_all_chapters": len(manuscript.chapters) == len(run.chapters) and manuscript.output_chars <= 30_000,
        "qa_map_keeps_all_chapters": len(manuscript_qa.chapters) == len(run.chapters) and manuscript_qa.output_chars <= 30_000,
    }
    passed_count = sum(1 for passed in checks.values() if passed)
    return {
        "passed": passed_count == len(checks),
        "score": f"{passed_count}/{len(checks)}",
        "chapter_count": total,
        "checks": checks,
        "observations": {
            "memory_source_chars": memory.source_chars,
            "memory_output_chars": memory.output_chars,
            "recall_chapters": [row.get("chapter_number") for row in recalled_rows],
            "tarin_last_touched_chapter": tarin.get("last_touched_chapter"),
            "tarin_dormant_for_chapters": tarin.get("dormant_for_chapters"),
            "tarin_future_touches": tarin.get("upcoming_planned_touches", []),
            "adaptive_target_words": pacing.get("adaptive_target_this_chapter"),
            "target_reachable_with_configured_max": pacing.get("target_reachable_with_configured_max"),
            "ending_phase": convergence.get("phase"),
            "developmental_context_mode": manuscript.mode,
            "developmental_context_chars": manuscript.output_chars,
            "qa_context_mode": manuscript_qa.mode,
            "qa_context_chars": manuscript_qa.output_chars,
        },
    }


def main() -> None:
    report = run_longform_benchmark()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
