from __future__ import annotations

import json

from novel_generator.dependencies import get_session_factory
from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.repositories import create_project, create_run, get_run, recover_running_runs
from novel_generator.schemas import ProjectCreate, RunCreate
from novel_generator.services.pipeline import process_run_safe
from novel_generator.services.state import approve_outline_review
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
        }
    )


def _critique_json(*, revision_required: bool) -> str:
    return json.dumps(
        {
            "strengths": ["The chapter advances the plot."],
            "warnings": ["The first draft needs sharper character distinction."] if revision_required else [],
            "revision_required": revision_required,
            "focus": ["Differentiate Iris from Tarin."] if revision_required else [],
            "forward_motion_score": 8,
            "ending_concreteness_score": 4 if revision_required else 8,
            "cost_consequence_realism_score": 7,
            "side_character_independence_score": 6,
            "proper_noun_continuity_score": 8,
            "repetition_risk_score": 3,
            "emotional_depth_score": 7 if revision_required else 8,
            "ideology_clarity_score": 7 if revision_required else 8,
            "civilian_texture_score": 6 if revision_required else 8,
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
        }
    )


class FakeOllamaClient:
    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def chat(self, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        return next(self._responses)


def _create_project(session, *, requested_chapters: int = 2):
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
            story_brief={
                "setting": "A failing memory-city",
                "tone": "Tense luminous sci-fi",
                "protagonist": "Iris, disgraced archivist",
                "core_conflict": "Save the city without accepting coercive control",
                "ending_target": "One clear ending centered on consent and sacrifice",
            },
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
                    "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor and the next problem 1 is the only route left.",
                    _critique_json(revision_required=False),
                    "Iris discovers the living map and commits to following it underground.",
                    _continuity_json(1),
                    _plan_json(2),
                    (
                        "Iris and Tarin move through the shelter corridor where families trade broth cups beside failing heat cloths. "
                        "Tarin resists the pull to run, then demands to know why frightened civilians should pay for Iris's truth, and Iris admits she fears obedience more than collapse. "
                        "Trigger 2 hits when the visible actor 2 wakes beneath them and the next problem 2 forces a deeper descent."
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
                    "Iris and Tarin reach the hatch while Tarin resists trusting the map. Trigger 1 lands when the visible actor 1 blocks the exit and the next problem 1 is immediate.",
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
                        "Iris slips out of the archive while Tarin resists following her. Trigger 1 arrives when the visible actor 1 seals the corridor and the next problem 1 is the only route left.",
                        _critique_json(revision_required=False),
                        "Iris discovers the living map and commits to following it underground.",
                        _continuity_json(1),
                        _plan_json(2),
                        (
                            "Iris and Tarin move through the shelter corridor where families trade broth cups beside failing heat cloths. "
                            "Tarin resists the pull to run, then demands to know why frightened civilians should pay for Iris's truth, and Iris admits she fears obedience more than collapse. "
                            "Trigger 2 hits when the visible actor 2 wakes beneath them and the next problem 2 forces a deeper descent."
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
