from __future__ import annotations

import json
from datetime import datetime, timedelta

from novel_generator.dependencies import get_session_factory
from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.repositories import create_project, create_run, get_run, list_stage_attempts, recover_running_runs
from novel_generator.schemas import ProjectCreate, RunCreate
from novel_generator.services.pipeline import process_run_safe
from novel_generator.services.runner import recover_incomplete_runs
from novel_generator.services.state import approve_outline_review, resume_failed_run
from novel_generator.settings import get_settings


def _story_bible_json() -> str:
    return json.dumps(
        {
            "logline": "A disgraced archivist finds a living map under a failing city.",
            "theme": "Control without consent becomes another form of ruin.",
            "act_plan": ["Discovery", "Descent", "Choice"],
            "cast": [
                {
                    "name": "Iris",
                    "role": "Archivist",
                    "desire": "Restore the city without surrendering herself to it",
                    "risk": "Becoming the tool of the system she hates",
                },
                {
                    "name": "Tarin",
                    "role": "Guide",
                    "desire": "Survive the undercity",
                    "risk": "Trusting the wrong version of Iris",
                },
            ],
            "character_agendas": [
                {
                    "name": "Iris",
                    "want": "Restore the city without surrendering agency",
                    "fear": "Becoming another servant of the lattice",
                    "line_in_sand": "She will not erase consent for peace.",
                    "stance_on_core_conflict": "Freedom matters more than imposed order.",
                    "relationship_to_protagonist": "Self",
                    "public_belief": "No city deserves survival through coerced obedience.",
                    "private_pressure": "Iris worries the city may collapse if she is wrong.",
                    "stress_response": "She doubles down on control when afraid.",
                },
                {
                    "name": "Tarin",
                    "want": "Stay alive long enough to leave the city",
                    "fear": "Being manipulated into loyalty",
                    "line_in_sand": "He will not trust any sentient system twice.",
                    "stance_on_core_conflict": "Order is acceptable only if it is chosen.",
                    "relationship_to_protagonist": "Reluctant ally",
                    "public_belief": "Order only counts if people still get to choose it.",
                    "private_pressure": "He fears chaos because he has survived a module collapse before.",
                    "stress_response": "He hardens and withdraws trust under pressure.",
                },
            ],
            "canon_registry": [
                {"name": "Living Map", "kind": "system", "role": "Sentient guide lattice", "aliases": ["the map"]},
                {"name": "Undercity Archive", "kind": "location", "role": "Buried civic archive", "aliases": ["the archive"]},
            ],
            "conflict_ladder": [
                "Iris discovers the map is alive.",
                "The archive turns against her.",
                "The city forces a consent-versus-control decision.",
            ],
            "world_rules": ["The city stores memory in living stone."],
            "core_system_rules": ["The map can rewrite routes and access memories embedded in walls."],
            "prose_guardrails": [
                "Do not end chapters with abstract future-stakes summaries.",
                "Force technical wins to cost access, trust, or safety.",
                "Every fourth chapter must breathe and reconnect the crisis to human life.",
            ],
            "ending_promise": "Iris must choose between saving the city and keeping free will intact.",
        }
    )


def _outline_json(chapters: int) -> str:
    outcome_types = ["setback", "reversal", "win", "setback", "reversal"]
    chapter_modes = ["systems_crisis", "aftermath", "investigation", "breather", "reversal"]
    civilian_details = [
        "Families ration heat tabs in the archive corridor.",
        "Shelter families trade broth cups beside failing heat cloths.",
        "Exhausted workers pass bread tokens through the checkpoint queue.",
        "Children wait for filtered water while elders swap blankets in silence.",
        "Repair crews sleep in public transit pods while vendors count the dark stalls.",
    ]
    emotional_reveals = [
        "Iris admits the living map has started to feel like a grief she can touch.",
        "Iris admits she fears obedience more than collapse.",
        "Tarin admits he would rather flee than trust another sentient system.",
        "Iris admits she no longer trusts her own memory of the city.",
        "Tarin admits order only feels safe when someone else pays for it.",
    ]
    ideology_pressures = [
        "Tarin pushes Iris to justify risking frightened civilians for buried truth.",
        "Tarin demands to know why frightened civilians should pay for Iris's truth.",
        "A foreman argues that consent is meaningless if the pumps fail by dawn.",
        "A shelter medic says freedom speeches mean nothing without breathable air.",
        "A transit chief insists that survival first is still a moral choice.",
    ]
    payload = {
        "chapters": [
            {
                "chapter_number": index,
                "act": "Act I" if index == 1 else ("Act II" if index < chapters else "Act III"),
                "title": f"Chapter {index} Title",
                "objective": f"Objective {index}",
                "conflict_turn": f"Conflict turn {index}",
                "character_turn": f"Character turn {index}",
                "reveal": f"Reveal {index}",
                "ending_state": f"Ending state {index}",
                "outcome_type": outcome_types[index - 1] if index - 1 < len(outcome_types) else ("reversal" if index == chapters else "setback"),
                "primary_obstacle": f"Primary obstacle {index}",
                "cost_if_success": f"Cost if success {index}",
                "side_character_friction": f"Tarin resists in chapter {index}",
                "independent_side_character_move": "Tarin resists",
                "concrete_ending_hook": {
                    "trigger": f"Trigger {index}",
                    "visible_object_or_actor": f"Visible actor {index}",
                    "next_problem": f"Next problem {index}",
                },
                "chapter_mode": chapter_modes[index - 1] if index - 1 < len(chapter_modes) else ("breather" if index % 4 == 0 else "systems_crisis"),
                "civilian_life_detail": civilian_details[index - 1] if index - 1 < len(civilian_details) else f"Civilian detail {index}",
                "emotional_reveal": emotional_reveals[index - 1] if index - 1 < len(emotional_reveals) else f"Emotional reveal {index}",
                "ideology_pressure": ideology_pressures[index - 1] if index - 1 < len(ideology_pressures) else f"Ideology pressure {index}",
            }
            for index in range(1, chapters + 1)
        ]
    }
    return json.dumps(payload)


def _valid_outline_entry(index: int, total_chapters: int) -> dict[str, object]:
    chapter_modes = [
        "systems_crisis",
        "investigation",
        "aftermath",
        "interpersonal_confrontation",
        "physical_escape",
        "moral_negotiation",
        "breather",
        "reversal",
    ]
    outcome_type = "reversal" if index == total_chapters or index % 5 == 0 else ("setback" if index % 2 == 0 else "compromise")
    return {
        "chapter_number": index,
        "act": "Act I" if index <= total_chapters // 4 else ("Act II" if index < total_chapters else "Act III"),
        "title": f"Pressure Point {index}",
        "objective": f"Iris pursues step {index} without repeating the inciting incident.",
        "conflict_turn": f"The living map forces a new cost in chapter {index}.",
        "character_turn": f"Iris changes tactics after Tarin challenges the price of progress in chapter {index}.",
        "reveal": f"A hidden limit of the living map becomes visible in chapter {index}.",
        "ending_state": f"Chapter {index} leaves the route altered in a way Iris cannot undo.",
        "outcome_type": outcome_type,
        "primary_obstacle": f"A civic barrier blocks route {index}.",
        "cost_if_success": f"Progress in chapter {index} costs access, trust, or safety.",
        "side_character_friction": f"Tarin resists because chapter {index} endangers frightened civilians.",
        "independent_side_character_move": f"Tarin redirects the route token in chapter {index}.",
        "concrete_ending_hook": {
            "trigger": f"A visible consequence of chapter {index} arrives.",
            "visible_object_or_actor": f"Visible actor {index}",
            "next_problem": f"The next chapter must answer cost {index}.",
        },
        "chapter_mode": chapter_modes[(index - 1) % len(chapter_modes)],
        "civilian_life_detail": f"Families adapt around the fallout from chapter {index}.",
        "emotional_reveal": f"Iris admits a private fear that complicates choice {index}.",
        "ideology_pressure": "Consent matters even when delay is dangerous.",
        "genre_specific_beats": [f"The clue chain advances through chapter {index}."],
        "genre_state_change": f"The living map pressure advances in chapter {index}.",
    }


def _valid_outline_chunk_json(start_chapter: int, end_chapter: int, total_chapters: int = 64) -> str:
    return json.dumps(
        {
            "chapters": [
                _valid_outline_entry(index, total_chapters)
                for index in range(start_chapter, end_chapter + 1)
            ]
        }
    )


def _valid_outline_chunk_responses(total_chapters: int = 64) -> list[str]:
    return [
        _valid_outline_chunk_json(start_chapter, min(total_chapters, start_chapter + 7), total_chapters)
        for start_chapter in range(1, total_chapters + 1, 8)
    ]


def _story_turn_payload(index: int) -> dict[str, object]:
    turns: dict[int, dict[str, object]] = {
        1: {
            "irreversible_change": "Iris burns the archive master key to open the sealed maintenance stair.",
            "protagonist_choice": "Iris sacrifices her last bargaining token instead of waiting for council permission.",
            "choice_alternatives": ["Iris could hide the key and return to the council chamber."],
            "permanent_consequence": "The archive can never lock the stair again, and Tarin loses his council escort.",
            "why_this_chapter_cannot_be_cut": "Without the burned key, the stair remains sealed and the underground route never exists.",
            "state_before": "The archive key still controls every lower stair.",
            "state_after": "The maintenance stair is open, the key is ash, and Tarin is separated from his escort.",
        },
        2: {
            "irreversible_change": "Iris signs the shelter families onto the living map, exposing their names to the buried system.",
            "protagonist_choice": "Iris shares the route with the civilians instead of keeping it between herself and Tarin.",
            "choice_alternatives": ["Iris could leave the families unmarked and move through the hatch alone."],
            "permanent_consequence": "The buried system now tracks the shelter by name and demands Iris answer for them.",
            "why_this_chapter_cannot_be_cut": "Without the named families on the map, Iris reaches the hatch without owing them protection.",
            "state_before": "The shelter families are invisible to the map.",
            "state_after": "The families glow on the map and the lower hatch recognizes Iris as their sponsor.",
        },
    }
    return turns.get(
        index,
        {
            "irreversible_change": f"Iris breaks beacon-{index} to reveal vault-{index}.",
            "protagonist_choice": f"Iris chooses the exposed vault-{index} route over retreat.",
            "choice_alternatives": [f"Iris could leave beacon-{index} untouched and withdraw."],
            "permanent_consequence": f"Vault-{index} stays visible and cannot be hidden from Tarin.",
            "why_this_chapter_cannot_be_cut": f"Without beacon-{index} breaking, vault-{index} never enters the route.",
            "state_before": f"Beacon-{index} hides the vault path.",
            "state_after": f"Vault-{index} is visible and the beacon is broken.",
        },
    )


def _plan_json(index: int) -> str:
    return json.dumps(
        {
            "opening_state": f"Opening state {index}",
            "character_goal": f"Character goal {index}",
            "scene_beats": [f"Beat {index}.1", f"Beat {index}.2", f"Beat {index}.3", f"Beat {index}.4"],
            "conflict_turn": f"Conflict turn {index}",
            "ending_hook": f"Ending hook {index}",
            "attempt": f"Attempt {index}",
            "complication": f"Complication {index}",
            "price_paid": f"Price paid {index}",
            "partial_failure_mode": f"Partial failure {index}",
            "ending_hook_delivery": f"Ending hook delivery {index}",
            "emotional_anchor": f"Emotional anchor {index}",
            "civilian_texture": f"Civilian texture {index}",
            "ideology_clash": f"Ideology clash {index}",
            "primary_interpersonal_conflict": f"Primary interpersonal conflict {index}",
            "independent_side_character_move": "Tarin resists",
            "story_turn": _story_turn_payload(index),
        }
    )


def _critique_json(
    *,
    revision_required: bool,
    ending_hook_type: str | None = None,
    scene_turn_resolution_score: int | None = None,
    technical_escalation_fatigue_score: int = 0,
    side_character_independence_score: int = 6,
) -> str:
    return json.dumps(
        {
            "strengths": ["The chapter advances the plot."],
            "warnings": ["The first draft needs sharper character distinction."] if revision_required else [],
            "revision_required": revision_required,
            "focus": ["Differentiate Iris from Tarin."] if revision_required else [],
            "ending_hook_type": ending_hook_type
            or ("abstract_cliffhanger" if revision_required else "concrete_action_hook"),
            "forward_motion_score": 8,
            "ending_concreteness_score": 4 if revision_required else 8,
            "scene_turn_resolution_score": scene_turn_resolution_score
            if scene_turn_resolution_score is not None
            else (4 if revision_required else 8),
            "cost_consequence_realism_score": 7,
            "side_character_independence_score": side_character_independence_score,
            "proper_noun_continuity_score": 8,
            "repetition_risk_score": 3,
            "emotional_depth_score": 7 if revision_required else 8,
            "ideology_clarity_score": 7 if revision_required else 8,
            "civilian_texture_score": 6 if revision_required else 8,
            "technical_escalation_fatigue_score": technical_escalation_fatigue_score,
            "blocking_issues": ["The ending is still too abstract."] if revision_required else [],
            "soft_warnings": ["Tarin should push back harder."] if revision_required else [],
            "repair_scope": "targeted_scene_and_ending" if revision_required else "none",
        }
    )


def _continuity_json(index: int) -> str:
    return json.dumps(
        {
            "chapter_outcome": f"Outcome {index}",
            "current_patch_status": f"Patch status after chapter {index}",
            "character_states": {"Iris": f"State {index}", "Tarin": f"Support state {index}"},
            "world_state": f"World state {index}",
            "open_threads": [f"Open thread {index}"],
            "resolved_threads": [f"Resolved thread {index}"],
            "timeline_entry": f"Timeline entry {index}",
            "timeline": [f"Timeline entry {value}" for value in range(1, index + 1)],
            "new_entities_introduced": [
                {
                    "name": f"Entity {index}",
                    "kind": "project",
                    "role": f"Role {index}",
                    "aliases": [f"Alias {index}"],
                }
            ],
            "entity_state_changes": {f"Entity {index}": f"State change {index}"},
            "open_promises_by_name": {f"Promise {index}": f"Why promise {index} is still live"},
            "ideology_state_by_character": {
                "Iris": f"Ideology state {index} for Iris",
                "Tarin": f"Ideology state {index} for Tarin",
            },
            "ideology_shift_notes": {"Iris": f"Shift note {index}"},
            "memory_damage": {"Iris": f"Memory damage {index}"},
            "trust_fractures": {"Iris/Tarin": f"Trust fracture {index}"},
            "civilian_pressure_points": [f"Civilian pressure {index}"],
            "emotional_open_loops": {"Iris": f"Emotional loop {index}"},
            "side_character_decisions": {"Tarin": [f"Tarin resists in chapter {index}"]},
            "system_state_transitions": [
                {
                    "system_name": "Living Map",
                    "previous_state": "Sentient guide lattice" if index == 1 else f"Map state {index - 1}",
                    "new_state": f"Map state {index}",
                    "cause": f"Chapter {index} forces the map to reveal a new route.",
                    "chapter_number": index,
                }
            ],
            "story_turn": _story_turn_payload(index),
        }
    )


def _collision_continuity_json(index: int) -> str:
    payload = json.loads(_continuity_json(index))
    payload["new_entities_introduced"] = [
        {
            "name": f"Conflicting Entity {index}",
            "kind": "project",
            "role": "Conflicting alias owner",
            "aliases": ["Living Map"],
        }
    ]
    return json.dumps(payload)


def _qa_report_json() -> str:
    return json.dumps(
        {
            "overall_verdict": "Promising first draft with manageable repetition risk.",
            "strengths": ["The premise stays clear."],
            "warnings": ["The middle could use more concrete physical conflict."],
            "continuity_risks": ["Track how the map changes access permissions."],
            "repetition_risks": ["Watch for repeated control-versus-freedom phrasing."],
            "ending_coherence_notes": ["The ending promise is present and singular."],
            "lint_findings": [],
            "chapter_ending_quality_notes": ["One ending beat still needs more object-level specificity."],
            "easy_win_warnings": ["One technical escape still feels too smooth."],
            "proper_noun_continuity_findings": ["Track whether the map and Living Map are treated as the same system."],
            "side_character_agency_notes": ["Tarin still needs one harder refusal."],
            "atmospheric_repetition_findings": ["Watch for repeated luminous-stone phrasing."],
            "emotional_pacing_notes": ["The story needs one more breather after the midpoint surge."],
            "ideology_consistency_findings": ["Track whether Tarin's survival doctrine hardens intentionally."],
            "civilian_texture_findings": ["Show more concrete shelter life when the city locks down."],
            "technical_escalation_fatigue_findings": ["Watch for stacked alert language in late Act II."],
            "crisis_loop_findings": ["Chapters 1, 2 repeat crisis-loop pattern: alarm/warning activation -> lockdown/drone response."],
            "story_turn_quality_notes": ["Chapter turns are mostly non-cuttable."],
        }
    )


def _developmental_rewrite_json() -> str:
    return json.dumps(
        {
            "overall_diagnosis": "The draft needs one structural compression pass after QA.",
            "act_structure_notes": ["Act I works but should externalize the cost sooner."],
            "chapter_actions": [
                {
                    "chapter_numbers": [1],
                    "action": "rewrite",
                    "reason": "Make the archive breach cost a named relationship.",
                    "required_story_change": "Force Iris to burn the archive key in view of Tarin.",
                    "permanent_consequence": "The lower stair stays open and Tarin loses his escort.",
                }
            ],
            "merge_candidates": [],
            "cut_candidates": [],
            "continuity_repairs": ["Track the burned archive key in the ledger."],
            "theme_arc_repairs": ["Tie consent to a visible loss of access."],
            "prose_pattern_repairs": ["Remove future-hangs summary language."],
            "pre_rewrite_risks": ["One technical escape is too smooth."],
            "post_rewrite_risk_targets": ["Revised chapter should show a distinct permanent consequence."],
        }
    )


class FakeOllamaClient:
    def __init__(self, responses: list[str | Exception]):
        self._responses = iter(responses)

    def chat(self, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def _create_project(session, *, requested_chapters: int = 2, approved_canon: list[dict] | None = None):
    story_brief = {
        "setting": "A failing memory-city",
        "tone": "Tense luminous sci-fi",
        "protagonist": "Iris, disgraced archivist",
        "core_conflict": "Save the city without accepting coercive control",
        "ending_target": "One clear ending centered on consent and sacrifice",
    }
    if approved_canon is not None:
        story_brief["approved_canon"] = approved_canon
    return create_project(
        session,
        ProjectCreate(
            title="The Glass Orchard",
            premise="A disgraced archivist finds a living map under a failing city.",
            desired_word_count=2000,
            requested_chapters=requested_chapters,
            min_words_per_chapter=900,
            max_words_per_chapter=1200,
            preferred_model="test-model",
            story_brief=story_brief,
        ),
    )


def test_process_run_safe_pauses_after_outline_when_requested(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(session, run, settings, FakeOllamaClient([_story_bible_json(), _outline_json(2)]))

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.AWAITING_APPROVAL
        assert refreshed.current_step == "outline_review"
        assert refreshed.story_bible is not None
        assert refreshed.outline is not None
        assert len(refreshed.chapters) == 2
        assert len(refreshed.artifacts) == 0


def test_stage_attempts_are_recorded_for_successful_model_calls(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(session, run, settings, FakeOllamaClient([_story_bible_json(), _outline_json(2)]))

        attempts = list_stage_attempts(session, run.id)
        assert [(attempt.stage, attempt.status) for attempt in attempts] == [
            ("story_bible", "success"),
            ("outline", "success"),
        ]
        assert all(attempt.output_chars > 0 for attempt in attempts)
        assert attempts[0].attempt_metadata["label"] == "story bible"


def test_stage_attempts_are_recorded_for_failed_provider_calls(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient([RuntimeError("provider unavailable"), _outline_json(2)]),
        )

        refreshed = get_run(session, run.id)
        assert refreshed is not None
        assert refreshed.status == RunStatus.AWAITING_APPROVAL
        attempts = list_stage_attempts(session, run.id)
        assert attempts[0].stage == "story_bible"
        assert attempts[0].status == "failed"
        assert attempts[0].error_type == "RuntimeError"
        assert "provider unavailable" in (attempts[0].error_message or "")


def test_failed_draft_can_resume_from_saved_chapter_plan(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient([_story_bible_json(), _outline_json(1), _plan_json(1), RuntimeError("draft down")]),
        )

        failed = get_run(session, run.id)
        assert failed is not None
        assert failed.status == RunStatus.FAILED
        assert failed.chapters[0].plan is not None
        assert failed.chapters[0].content is None

        resume_failed_run(session, failed)
        session.commit()
        process_run_safe(
            session,
            failed,
            settings,
            FakeOllamaClient(
                [
                    "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them.",
                    _critique_json(revision_required=False),
                    "Iris chooses the lower route and accepts the cost of leaving the archive behind.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed is not None
        assert refreshed.status == RunStatus.COMPLETED
        attempts = list_stage_attempts(session, run.id)
        assert sum(attempt.stage == "chapter_plan" for attempt in attempts) == 1
        assert any(event.event_type == "run_resume_queued" for event in refreshed.events)
        assert any(event.event_type == "chapter_plan_checkpoint_reused" for event in refreshed.events)


def test_summary_fallback_completes_run_when_summary_provider_fails(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them.",
                    _critique_json(revision_required=False),
                    RuntimeError("summary down"),
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed is not None
        assert refreshed.status == RunStatus.COMPLETED
        assert any(event.event_type == "chapter_summary_fallback" for event in refreshed.events)
        assert refreshed.chapters[0].summary


def test_continuity_fallback_completes_run_when_continuity_provider_fails(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them.",
                    _critique_json(revision_required=False),
                    "Iris chooses the lower route and accepts the cost of leaving the archive behind.",
                    RuntimeError("continuity down"),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed is not None
        assert refreshed.status == RunStatus.COMPLETED
        assert any(event.event_type == "continuity_update_fallback" for event in refreshed.events)
        assert refreshed.chapters[0].continuity_update


def test_large_run_generates_outline_in_chunks_and_pauses(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=64)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient([_story_bible_json(), *_valid_outline_chunk_responses()]),
        )

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.AWAITING_APPROVAL
        assert refreshed.current_step == "outline_review"
        assert refreshed.outline is not None
        assert len(refreshed.outline) == 64
        assert [entry["chapter_number"] for entry in refreshed.outline] == list(range(1, 65))
        assert len(refreshed.chapters) == 64
        assert sum(event.event_type == "outline_chunk_completed" for event in refreshed.events) == 8


def test_large_outline_chunk_fallback_prevents_short_outline_failure(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=64)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    json.dumps({"chapters": []}),
                    json.dumps({"chapters": []}),
                    *_valid_outline_chunk_responses()[1:],
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.AWAITING_APPROVAL
        assert refreshed.outline is not None
        assert len(refreshed.outline) == 64
        assert [entry["chapter_number"] for entry in refreshed.outline] == list(range(1, 65))
        assert any(event.event_type == "outline_chunk_fallback" for event in refreshed.events)


def test_story_bible_fallback_prevents_shape_failure(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(["[]", "[]", _outline_json(2)]),
        )

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.AWAITING_APPROVAL
        assert refreshed.story_bible["logline"] == project.premise
        assert any(event.event_type == "story_bible_fallback" for event in refreshed.events)


def test_process_run_merges_approved_project_canon_into_story_bible(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(
            session,
            requested_chapters=2,
            approved_canon=[
                {
                    "name": "Glass Orchard",
                    "kind": "location",
                    "role": "Locked project-level setting term",
                    "aliases": ["the orchard"],
                    "approved": True,
                    "locked": True,
                }
            ],
        )
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(session, run, settings, FakeOllamaClient([_story_bible_json(), _outline_json(2)]))

        refreshed = get_run(session, run.id)
        assert refreshed is not None
        canon_by_name = {entity["name"]: entity for entity in refreshed.story_bible["canon_registry"]}
        assert canon_by_name["Glass Orchard"]["approved"] is True
        assert canon_by_name["Glass Orchard"]["locked"] is True
        assert refreshed.continuity_ledger["active_entities"][0]["name"] == "Glass Orchard"


def test_approved_run_completes_and_generates_qa_report(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(session, run, settings, FakeOllamaClient([_story_bible_json(), _outline_json(2)]))

        paused = get_run(session, run.id)
        assert paused is not None
        approve_outline_review(session, paused)
        session.commit()

        resume_client = FakeOllamaClient(
                [
                    _plan_json(1),
                    "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them.",
                    _critique_json(revision_required=False),
                    "Iris discovers the living map and commits to following it underground.",
                    _continuity_json(1),
                    _plan_json(2),
                    (
                        "Iris and Tarin move through the shelter corridor where families trade broth cups beside failing heat cloths. "
                        "Tarin resists the pull to run, then demands to know why frightened civilians should pay for Iris's truth, and Iris admits she fears obedience more than collapse. "
                        "Trigger 2 hits when the visible actor 2 wakes beneath them and a lower hatch opens."
                    ),
                    _critique_json(revision_required=False),
                    "Iris and Tarin descend farther and realize the city has been steering them.",
                    _continuity_json(2),
                    _qa_report_json(),
                ]
        )
        process_run_safe(session, paused, settings, resume_client)

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.COMPLETED
        assert len(refreshed.chapters) == 2
        assert all(chapter.status == ChapterStatus.COMPLETED for chapter in refreshed.chapters)
        assert all(chapter.content for chapter in refreshed.chapters)
        assert all(chapter.summary for chapter in refreshed.chapters)
        assert all(chapter.continuity_update for chapter in refreshed.chapters)
        assert len(refreshed.artifacts) == 3
        assert any(artifact.kind == "qa-report" for artifact in refreshed.artifacts)
        assert refreshed.summary_context is not None


def test_developmental_rewrite_pass_exports_report_and_revised_outline(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(
                project_id=project.id,
                model_name="test-model",
                pause_after_outline=False,
                developmental_rewrite_enabled=True,
            ),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    (
                        "Iris slips out of the archive while Tarin resists following her. "
                        "Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them."
                    ),
                    _critique_json(revision_required=False),
                    "Iris discovers the living map and commits to following it underground.",
                    _continuity_json(1),
                    _qa_report_json(),
                    _developmental_rewrite_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.COMPLETED
        artifact_kinds = {artifact.kind for artifact in refreshed.artifacts}
        assert "developmental-rewrite-report" in artifact_kinds
        assert "revised-outline" in artifact_kinds
        assert "developmental-qa-report" in artifact_kinds
        assert any(event.event_type == "developmental_rewrite_completed" for event in refreshed.events)


def test_manuscript_qa_fallback_completes_when_model_output_stays_invalid(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    (
                        "Iris slips out of the archive while Tarin resists following her. "
                        "Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them."
                    ),
                    _critique_json(revision_required=False),
                    "Iris discovers the living map and commits to following it underground.",
                    _continuity_json(1),
                    "not valid json",
                    "still not valid json",
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.COMPLETED
        assert any(artifact.kind == "qa-report" for artifact in refreshed.artifacts)
        assert any(event.event_type == "manuscript_qa_fallback" for event in refreshed.events)


def test_revision_pass_updates_chapter_and_continuity_ledger(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    "Chapter 1\n\nIris slips into the shaft, talks in circles, and thinks the next step would decide everything.",
                    _critique_json(revision_required=True),
                    "Chapter 1: Revision\n\nThe revised draft sharpens Iris and Tarin into distinct voices. Tarin refuses to trust the map, and Trigger 1 lands when the visible actor 1 seals the hatch and the next problem 1 is immediate.",
                    _critique_json(revision_required=False),
                    "Iris chooses the undercity mission and Tarin finally believes her.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        chapter = refreshed.chapters[0]
        assert refreshed.status == RunStatus.COMPLETED
        assert chapter.qa_notes is not None
        assert chapter.qa_notes["revision_required"] is True
        assert chapter.qa_notes["repair_scope"] == "targeted_scene_and_ending"
        assert chapter.qa_notes["blocking_issues"]
        assert chapter.content.startswith("The revised draft")
        assert "Chapter 1" not in chapter.content
        assert refreshed.continuity_ledger["current_patch_status"] == "Patch status after chapter 1"
        assert refreshed.continuity_ledger["active_entities"]
        assert refreshed.continuity_ledger["memory_damage"]["Iris"] == "Memory damage 1"


def test_style_lint_triggers_voice_and_texture_revision(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    (
                        "Iris felt fear in the archive. Iris saw panic rising. Iris knew dread had settled. "
                        "Iris thought grief would break her. Iris noticed guilt in the silence. "
                        "Iris wondered if shame had won. Iris heard terror under every breath. "
                        "Despair made the hallway impossible to name. Tarin resists in chapter 1 as Iris tries to run. "
                        "Trigger 1 lands when the visible actor 1 seals the corridor, forcing a deeper descent."
                    ),
                    _critique_json(revision_required=False),
                    (
                        "Iris slowed at the shaft while Tarin blocked the route with one hand on the rail. "
                        "The air tasted of rust, and each blue pulse from the map caught in the water on the floor. "
                        "Tarin refused to move until Iris admitted what following the map would cost him. "
                        "Trigger 1 lands when the visible actor 1 seals the corridor, forcing a deeper descent."
                    ),
                    _critique_json(revision_required=False),
                    "Iris accepts Tarin's resistance and follows the sealed corridor at a cost.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        chapter = refreshed.chapters[0]
        assert refreshed.status == RunStatus.COMPLETED
        assert chapter.content.startswith("Iris slowed at the shaft")
        assert any(
            event.event_type == "chapter_revision_started"
            and event.payload.get("repair_scope") == "voice_and_texture"
            for event in refreshed.events
        )


def test_ending_score_triggers_targeted_scene_revision(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    (
                        "Iris put the burned key into Tarin's hand. "
                        "Trigger 1 lands when the visible actor 1 seals the corridor, leaving only route 1."
                    ),
                    _critique_json(
                        revision_required=False,
                        ending_hook_type="outline_summary",
                        scene_turn_resolution_score=4,
                    ),
                    (
                        "Iris stopped at the rail while Tarin blocked the route with his shoulder. "
                        "The visible actor 1 sealed the corridor behind them, and Iris handed Tarin the burned key."
                    ),
                    _critique_json(revision_required=False),
                    "Iris accepts the cost of the sealed corridor and gives Tarin the key.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        chapter = refreshed.chapters[0]
        assert refreshed.status == RunStatus.COMPLETED
        assert chapter.content.startswith("Iris stopped at the rail")
        assert any(
            event.event_type == "chapter_revision_started"
            and event.payload.get("repair_scope") == "targeted_scene_and_ending"
            for event in refreshed.events
        )


def test_technical_fatigue_score_triggers_targeted_revision(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    (
                        "Iris put the burned key into Tarin's hand. "
                        "Trigger 1 lands when the visible actor 1 seals the corridor, leaving only route 1."
                    ),
                    _critique_json(revision_required=False, technical_escalation_fatigue_score=8),
                    (
                        "Iris stopped at the rail while Tarin blocked the route with his shoulder. "
                        "The visible actor 1 sealed the corridor behind them, and Iris handed Tarin the burned key."
                    ),
                    _critique_json(revision_required=False),
                    "Iris accepts the cost of the sealed corridor and gives Tarin the key.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        chapter = refreshed.chapters[0]
        assert refreshed.status == RunStatus.COMPLETED
        assert chapter.content.startswith("Iris stopped at the rail")
        assert any(
            event.event_type == "chapter_revision_started"
            and event.payload.get("repair_scope") == "targeted_scene_and_ending"
            for event in refreshed.events
        )


def test_side_character_score_triggers_targeted_revision(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    (
                        "Iris put the burned key into Tarin's hand. "
                        "Trigger 1 lands when the visible actor 1 seals the corridor, leaving only route 1."
                    ),
                    _critique_json(revision_required=False, side_character_independence_score=4),
                    (
                        "Iris stopped at the rail while Tarin blocked the route with his shoulder. "
                        "The visible actor 1 sealed the corridor behind them, and Iris handed Tarin the burned key."
                    ),
                    _critique_json(revision_required=False),
                    "Iris accepts the cost of the sealed corridor and gives Tarin the key.",
                    _continuity_json(1),
                    _qa_report_json(),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        chapter = refreshed.chapters[0]
        assert refreshed.status == RunStatus.COMPLETED
        assert chapter.content.startswith("Iris stopped at the rail")
        assert any(
            event.event_type == "chapter_revision_started"
            and event.payload.get("repair_scope") == "targeted_scene_and_ending"
            for event in refreshed.events
        )


def test_canonical_entity_collision_hard_fails_run(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=1)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                [
                    _story_bible_json(),
                    _outline_json(1),
                    _plan_json(1),
                    "Iris and Tarin reach the hatch while Tarin resists trusting the map. Trigger 1 lands when the visible actor 1 blocks the exit and the route below opens.",
                    _critique_json(revision_required=False),
                    "Iris secures the map but exposes Tarin's route.",
                    _collision_continuity_json(1),
                ]
            ),
        )

        refreshed = get_run(session, run.id)
        assert refreshed.status == RunStatus.FAILED
        assert "Canonical entity collision" in (refreshed.error_message or "")


def test_continuity_ledger_carries_entities_forward_across_completed_chapters(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        session.commit()

        run = get_run(session, run.id)
        assert run is not None
        process_run_safe(
            session,
            run,
            settings,
            FakeOllamaClient(
                    [
                        _story_bible_json(),
                        _outline_json(2),
                        _plan_json(1),
                        "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor, leaving only route 1 below them.",
                        _critique_json(revision_required=False),
                        "Iris discovers the living map and commits to following it underground.",
                        _continuity_json(1),
                        _plan_json(2),
                        (
                            "Iris and Tarin move through the shelter corridor where families trade broth cups beside failing heat cloths. "
                            "Tarin resists the pull to run, then demands to know why frightened civilians should pay for Iris's truth, and Iris admits she fears obedience more than collapse. "
                            "Trigger 2 hits when the visible actor 2 wakes beneath them and a lower hatch opens."
                        ),
                        _critique_json(revision_required=False),
                        "Iris and Tarin descend farther and realize the city has been steering them.",
                        _continuity_json(2),
                        _qa_report_json(),
                    ]
            ),
        )

        refreshed = get_run(session, run.id)
        names = {entity["name"] for entity in refreshed.continuity_ledger["active_entities"]}
        assert refreshed.status == RunStatus.COMPLETED
        assert "Living Map" in names
        assert "Entity 1" in names
        assert "Entity 2" in names
        assert refreshed.continuity_ledger["trust_fractures"]["Iris/Tarin"] == "Trust fracture 2"
        assert refreshed.continuity_ledger["civilian_pressure_points"][-1] == "Civilian pressure 2"
        assert refreshed.continuity_ledger["side_character_decisions"]["Tarin"][-1].startswith("Tarin resists in chapter 2")
        assert refreshed.continuity_ledger["system_state_by_name"]["Living Map"] == "Map state 2"
        assert len(refreshed.continuity_ledger["system_state_transitions"]) == 2


def test_v1_regeneration_creates_fresh_v2_structure(configured_environment) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        source_run = create_run(
            session,
            project,
            RunCreate(project_id=project.id, model_name="test-model", pause_after_outline=False),
        )
        source_run.pipeline_version = 1
        source_run.outline = [{"chapter_number": 1, "title": "Legacy", "summary": "Old outline"}]
        source_run.status = RunStatus.COMPLETED
        session.flush()

        regenerated = create_run(
            session,
            project,
            RunCreate(
                project_id=project.id,
                model_name="test-model",
                source_run_id=source_run.id,
                resume_from_chapter=2,
            ),
        )
        session.commit()

        assert regenerated.pipeline_version == 2
        assert regenerated.source_run_id is None
        assert regenerated.resume_from_chapter is None
        assert regenerated.outline is None
        assert regenerated.story_bible is None


def test_recover_running_runs_marks_runs_as_queued(configured_environment) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        run.status = RunStatus.RUNNING
        session.commit()

        recovered = recover_running_runs(session)
        session.commit()

        refreshed = get_run(session, run.id)
        assert recovered == 1
        assert refreshed.status.value == "queued"
        assert refreshed.recovery_count == 1
        assert refreshed.events[-1].payload["recovery_reason"] == "worker_startup"


def test_recover_running_runs_uses_stale_heartbeat_threshold(configured_environment) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        stale = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        stale.status = RunStatus.RUNNING
        stale.last_heartbeat_at = datetime.utcnow() - timedelta(seconds=7200)
        fresh = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        fresh.status = RunStatus.RUNNING
        fresh.last_heartbeat_at = datetime.utcnow()
        session.commit()

        recovered = recover_running_runs(session, stale_after_seconds=3600, reason="stale_heartbeat")
        session.commit()

        refreshed_stale = get_run(session, stale.id)
        refreshed_fresh = get_run(session, fresh.id)
        assert recovered == 1
        assert refreshed_stale.status == RunStatus.QUEUED
        assert refreshed_stale.events[-1].payload["recovery_reason"] == "stale_heartbeat"
        assert refreshed_fresh.status == RunStatus.RUNNING


def test_worker_startup_recovery_only_requeues_stale_runs(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = _create_project(session, requested_chapters=2)
        stale = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        stale.status = RunStatus.RUNNING
        stale.last_heartbeat_at = datetime.utcnow() - timedelta(seconds=7200)
        fresh = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        fresh.status = RunStatus.RUNNING
        fresh.last_heartbeat_at = datetime.utcnow()
        session.commit()

        stale_id = stale.id
        fresh_id = fresh.id

    recover_incomplete_runs(settings)

    with session_factory() as session:
        refreshed_stale = get_run(session, stale_id)
        refreshed_fresh = get_run(session, fresh_id)
        assert refreshed_stale.status == RunStatus.QUEUED
        assert refreshed_stale.events[-1].payload["recovery_reason"] == "worker_startup"
        assert refreshed_fresh.status == RunStatus.RUNNING
