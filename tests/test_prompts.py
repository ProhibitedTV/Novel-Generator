from __future__ import annotations

from novel_generator.models import ChapterDraft, ChapterStatus, GenerationRun, Project
from novel_generator.schemas import ContinuityBibleRow, ManuscriptQaReport
from novel_generator.services.prompts import (
    build_chapter_critique_messages,
    build_chapter_draft_messages,
    build_chapter_plan_messages,
    build_chapter_revision_messages,
    build_continuity_update_messages,
    build_developmental_rewrite_messages,
    build_manuscript_qa_messages,
    build_story_bible_messages,
    parse_chapter_critique,
    parse_chapter_plan,
    parse_continuity_update,
    parse_developmental_rewrite_plan,
    parse_manuscript_qa_report,
    parse_outline,
    parse_story_bible,
    rolling_context,
    sanitize_chapter_content,
)


def test_story_bible_parser_accepts_valid_json_with_fences() -> None:
    parsed = parse_story_bible(
        """```json
        {
          "genre_profile": "mystery",
          "logline": "An archivist finds a living map under a failing city.",
          "theme": "Memory without agency becomes a prison.",
          "act_plan": ["Discovery", "Descent", "Confrontation"],
          "cast": [
            {"name": "Iris", "role": "Archivist", "desire": "Restore the city", "risk": "Becoming its servant"}
          ],
          "character_agendas": [
            {
              "name": "Iris",
              "want": "Restore the city",
              "fear": "Becoming its servant",
              "line_in_sand": "She will not erase consent to save order.",
              "stance_on_core_conflict": "Freedom matters more than imposed calm.",
              "relationship_to_protagonist": "Self",
              "public_belief": "No city should survive by stealing consent.",
              "private_pressure": "She fears the city may collapse without control.",
              "stress_response": "She overcommits and hides her panic."
            }
          ],
          "canon_registry": [
            {"name": "Living Map", "kind": "system", "role": "Sentient map", "aliases": ["Map"]}
          ],
          "conflict_ladder": ["The map awakens", "The city fights back", "Iris must choose who controls memory"],
          "world_rules": ["The city records memory in living stone."],
          "core_system_rules": ["Maps can rewrite routes and memories."],
          "prose_guardrails": ["No abstract ending thesis statements."],
          "genre_contract": ["Each reveal should be fairly planted."],
          "style_profile": {
            "narrative_voice": "Close third with sharp sensory pressure.",
            "sentence_rhythm": "Tight action beats broken by slower dread.",
            "imagery_palette": ["wet stone", "failing light"],
            "dialogue_rules": ["Subtext before confession"],
            "character_voice_map": {"Iris": "Precise under pressure"},
            "avoid": ["weight of everything"]
          },
          "ending_promise": "The city survives only if Iris gives up control."
        }
        ```"""
    )

    assert parsed.logline.startswith("An archivist")
    assert parsed.cast[0].name == "Iris"
    assert parsed.character_agendas[0].line_in_sand.startswith("She will not erase")
    assert parsed.canon_registry[0].name == "Living Map"
    assert parsed.genre_profile == "mystery"
    assert parsed.genre_contract == ["Each reveal should be fairly planted."]
    assert parsed.style_profile.narrative_voice.startswith("Close third")
    assert parsed.style_profile.character_voice_map["Iris"] == "Precise under pressure"
    assert parsed.ending_promise.endswith("control.")


def test_outline_parser_enforces_exact_count_and_keys() -> None:
    parsed = parse_outline(
        """
        {
          "chapters": [
            {
              "chapter_number": 1,
              "act": "Act I",
              "title": "Arrival",
              "objective": "Recover the hidden map.",
              "conflict_turn": "The archive locks Iris inside.",
              "character_turn": "Iris stops hiding how desperate she is.",
              "reveal": "The map recognizes Iris by name.",
              "ending_state": "Iris escapes with proof the map is alive.",
              "outcome_type": "setback",
              "primary_obstacle": "The archive shutters and isolates Iris.",
              "cost_if_success": "Iris burns her archivist credentials to escape.",
              "side_character_friction": "Tarin refuses to trust the map until Iris proves it is not manipulating her.",
              "concrete_ending_hook": {
                "trigger": "The map reroutes the exits.",
                "visible_object_or_actor": "A stone doorway folds shut.",
                "next_problem": "Iris has only one path left underground."
              },
              "chapter_mode": "systems_crisis",
              "civilian_life_detail": "Workers outside the archive swap ration-chits for warm tea before curfew.",
              "emotional_reveal": "Iris admits she is more afraid of obedience than death.",
              "ideology_pressure": "Tarin pushes Iris to justify risking civilians for the truth."
            },
            {
              "chapter_number": 2,
              "act": "Act I",
              "title": "Descent",
              "objective": "Trace the map beneath the city.",
              "conflict_turn": "The tunnels begin rewriting the route.",
              "character_turn": "Iris chooses trust over isolation.",
              "reveal": "The city has been steering her for years.",
              "ending_state": "Iris commits to the undercity mission.",
              "outcome_type": "reversal",
              "primary_obstacle": "The undercity mutates around Iris and Tarin.",
              "cost_if_success": "Tarin is exposed to the map's memory surge.",
              "side_character_friction": "Tarin wants to destroy the map instead of follow it.",
              "concrete_ending_hook": {
                "trigger": "A hidden elevator wakes below them.",
                "visible_object_or_actor": "Its lens locks onto Iris.",
                "next_problem": "The city has begun choosing for her."
              },
              "chapter_mode": "aftermath",
              "civilian_life_detail": "Families in the tunnel shelter wrap sleeping children in maintenance tarps.",
              "emotional_reveal": "Tarin confesses why he stopped trusting sentient systems.",
              "ideology_pressure": "Iris and Tarin clash over whether survival justifies coercion."
            }
          ]
        }
        """,
        requested_chapters=2,
    )

    assert len(parsed) == 2
    assert parsed[0]["title"] == "Arrival"
    assert parsed[1]["ending_state"] == "Iris commits to the undercity mission."


def test_outline_parser_rejects_wrong_chapter_count() -> None:
    try:
        parse_outline(
            """
            {
              "chapters": [
                {
                  "chapter_number": 1,
                  "act": "Act I",
                  "title": "Only Chapter",
                  "objective": "Start the story.",
                  "conflict_turn": "Trouble arrives.",
                  "character_turn": "The hero commits.",
                  "reveal": "The threat is personal.",
                  "ending_state": "The mission begins.",
                  "outcome_type": "reversal",
                  "primary_obstacle": "The station locks down.",
                  "cost_if_success": "The hero loses their credentials.",
                  "side_character_friction": "The pilot wants to run instead of investigate.",
                  "concrete_ending_hook": {
                    "trigger": "A dead channel crackles alive.",
                    "visible_object_or_actor": "The pilot's stolen comm unit",
                    "next_problem": "The mission is now public."
                  },
                  "chapter_mode": "systems_crisis",
                  "civilian_life_detail": "Dockworkers sleep under flickering loading lamps.",
                  "emotional_reveal": "The hero admits they are already too compromised to walk away.",
                  "ideology_pressure": "The pilot demands proof that truth matters more than survival."
                }
              ]
            }
            """,
            requested_chapters=2,
        )
    except ValueError as exc:
        assert "2 were required" in str(exc)
    else:
        raise AssertionError("Expected the outline parser to reject the wrong chapter count.")


def test_outline_parser_accepts_single_chapter_object_shape() -> None:
    parsed = parse_outline(
        """
        {
          "chapters": {
            "chapter_number": 1,
            "act": "Act I",
            "title": "Signal",
            "objective": "Trace the forbidden patch.",
            "conflict_turn": "The system locks Nora out.",
            "character_turn": "Nora stops hiding what she knows.",
            "reveal": "The patch is signed with her key.",
            "ending_state": "Nora commits to chasing the source.",
            "outcome_type": "reversal",
            "primary_obstacle": "Authority lockdowns close around Nora.",
            "cost_if_success": "Nora burns her admin credentials.",
            "side_character_friction": "Jun wants to destroy the evidence instead of trace it.",
            "concrete_ending_hook": {
              "trigger": "A drone reaches the hatch.",
              "visible_object_or_actor": "Its lens turns blue.",
              "next_problem": "It speaks in Nora's own voice."
            },
            "chapter_mode": "systems_crisis",
            "civilian_life_detail": "Night-shift cleaners share reheated broth between lock checks.",
            "emotional_reveal": "Nora admits she is terrified of becoming useful to the regime again.",
            "ideology_pressure": "Jun demands to know whether consent matters when the station could fail."
          }
        }
        """,
        requested_chapters=1,
    )

    assert len(parsed) == 1
    assert parsed[0]["chapter_number"] == 1
    assert parsed[0]["title"] == "Signal"


def test_outline_parser_accepts_number_keyed_chapter_dict() -> None:
    parsed = parse_outline(
        """
        {
          "outline": {
            "1": {
              "chapter_number": 1,
              "act": "Act I",
              "title": "Signal",
              "objective": "Trace the forbidden patch.",
              "conflict_turn": "The system locks Nora out.",
              "character_turn": "Nora stops hiding what she knows.",
              "reveal": "The patch is signed with her key.",
              "ending_state": "Nora commits to chasing the source.",
              "outcome_type": "setback",
              "primary_obstacle": "Authority lockdowns close around Nora.",
              "cost_if_success": "Nora burns her admin credentials.",
              "side_character_friction": "Jun wants to destroy the evidence instead of trace it.",
              "concrete_ending_hook": {
                "trigger": "A drone reaches the hatch.",
                "visible_object_or_actor": "Its lens turns blue.",
                "next_problem": "It speaks in Nora's own voice."
              },
              "chapter_mode": "systems_crisis",
              "civilian_life_detail": "Night-shift cleaners share reheated broth between lock checks.",
              "emotional_reveal": "Nora admits she is terrified of becoming useful to the regime again.",
              "ideology_pressure": "Jun demands to know whether consent matters when the station could fail."
            },
            "2": {
              "chapter_number": 2,
              "act": "Act I",
              "title": "Watchdog",
              "objective": "Prove the patch is manipulating compliance.",
              "conflict_turn": "The audit trail burns Jun's access.",
              "character_turn": "Nora accepts Jun may walk away.",
              "reveal": "The patch forks through a hidden watchdog.",
              "ending_state": "Nora now has one dangerous source node to chase.",
              "outcome_type": "reversal",
              "primary_obstacle": "The watchdog falsifies its own logs.",
              "cost_if_success": "Jun loses trusted access to the archive.",
              "side_character_friction": "Jun refuses to risk civilians for proof.",
              "concrete_ending_hook": {
                "trigger": "The source node wakes.",
                "visible_object_or_actor": "Its console floods blue.",
                "next_problem": "Authority now knows Nora is inside."
              },
              "chapter_mode": "aftermath",
              "civilian_life_detail": "Medics in the shelter count rationed oxygen masks by hand.",
              "emotional_reveal": "Jun admits why he no longer believes emergency rule ever gives power back.",
              "ideology_pressure": "Nora and Jun argue over whether order bought with fear is still survival."
            }
          }
        }
        """,
        requested_chapters=2,
    )

    assert len(parsed) == 2
    assert parsed[1]["title"] == "Watchdog"


def test_sanitize_chapter_content_removes_duplicate_heading() -> None:
    cleaned = sanitize_chapter_content("Chapter 7: Descent\n\nThe real prose starts here.")

    assert cleaned == "The real prose starts here."


def test_outline_parser_requires_breather_or_aftermath_every_four_chapters() -> None:
    try:
        parse_outline(
            """
            {
              "chapters": [
                {
                  "chapter_number": 1,
                  "act": "Act I",
                  "title": "One",
                  "objective": "One",
                  "conflict_turn": "One",
                  "character_turn": "One",
                  "reveal": "One",
                  "ending_state": "One",
                  "outcome_type": "setback",
                  "primary_obstacle": "One",
                  "cost_if_success": "One",
                  "side_character_friction": "One",
                  "concrete_ending_hook": {"trigger": "One", "visible_object_or_actor": "One", "next_problem": "One"},
                  "chapter_mode": "systems_crisis",
                  "civilian_life_detail": "One",
                  "emotional_reveal": "One",
                  "ideology_pressure": "One"
                },
                {
                  "chapter_number": 2,
                  "act": "Act I",
                  "title": "Two",
                  "objective": "Two",
                  "conflict_turn": "Two",
                  "character_turn": "Two",
                  "reveal": "Two",
                  "ending_state": "Two",
                  "outcome_type": "reversal",
                  "primary_obstacle": "Two",
                  "cost_if_success": "Two",
                  "side_character_friction": "Two",
                  "concrete_ending_hook": {"trigger": "Two", "visible_object_or_actor": "Two", "next_problem": "Two"},
                  "chapter_mode": "investigation",
                  "civilian_life_detail": "Two",
                  "emotional_reveal": "Two",
                  "ideology_pressure": "Two"
                },
                {
                  "chapter_number": 3,
                  "act": "Act II",
                  "title": "Three",
                  "objective": "Three",
                  "conflict_turn": "Three",
                  "character_turn": "Three",
                  "reveal": "Three",
                  "ending_state": "Three",
                  "outcome_type": "win",
                  "primary_obstacle": "Three",
                  "cost_if_success": "Three",
                  "side_character_friction": "Three",
                  "concrete_ending_hook": {"trigger": "Three", "visible_object_or_actor": "Three", "next_problem": "Three"},
                  "chapter_mode": "systems_crisis",
                  "civilian_life_detail": "Three",
                  "emotional_reveal": "Three",
                  "ideology_pressure": "Three"
                },
                {
                  "chapter_number": 4,
                  "act": "Act II",
                  "title": "Four",
                  "objective": "Four",
                  "conflict_turn": "Four",
                  "character_turn": "Four",
                  "reveal": "Four",
                  "ending_state": "Four",
                  "outcome_type": "setback",
                  "primary_obstacle": "Four",
                  "cost_if_success": "Four",
                  "side_character_friction": "Four",
                  "concrete_ending_hook": {"trigger": "Four", "visible_object_or_actor": "Four", "next_problem": "Four"},
                  "chapter_mode": "systems_crisis",
                  "civilian_life_detail": "Four",
                  "emotional_reveal": "Four",
                  "ideology_pressure": "Four"
                }
              ]
            }
            """,
            requested_chapters=4,
        )
    except ValueError as exc:
        assert "breather or aftermath" in str(exc)
    else:
        raise AssertionError("Expected the outline parser to require a breather or aftermath chapter.")


def test_outline_parser_rejects_chapter_mode_repeated_from_previous_two() -> None:
    try:
        parse_outline(
            """
            {
              "chapters": [
                {
                  "chapter_number": 1,
                  "act": "Act I",
                  "title": "One",
                  "objective": "One",
                  "conflict_turn": "One",
                  "character_turn": "One",
                  "reveal": "One",
                  "ending_state": "One",
                  "outcome_type": "setback",
                  "primary_obstacle": "One",
                  "cost_if_success": "One",
                  "side_character_friction": "One",
                  "concrete_ending_hook": {"trigger": "One", "visible_object_or_actor": "One", "next_problem": "One"},
                  "chapter_mode": "investigation",
                  "civilian_life_detail": "One",
                  "emotional_reveal": "One",
                  "ideology_pressure": "One"
                },
                {
                  "chapter_number": 2,
                  "act": "Act I",
                  "title": "Two",
                  "objective": "Two",
                  "conflict_turn": "Two",
                  "character_turn": "Two",
                  "reveal": "Two",
                  "ending_state": "Two",
                  "outcome_type": "reversal",
                  "primary_obstacle": "Two",
                  "cost_if_success": "Two",
                  "side_character_friction": "Two",
                  "concrete_ending_hook": {"trigger": "Two", "visible_object_or_actor": "Two", "next_problem": "Two"},
                  "chapter_mode": "aftermath",
                  "civilian_life_detail": "Two",
                  "emotional_reveal": "Two",
                  "ideology_pressure": "Two"
                },
                {
                  "chapter_number": 3,
                  "act": "Act II",
                  "title": "Three",
                  "objective": "Three",
                  "conflict_turn": "Three",
                  "character_turn": "Three",
                  "reveal": "Three",
                  "ending_state": "Three",
                  "outcome_type": "setback",
                  "primary_obstacle": "Three",
                  "cost_if_success": "Three",
                  "side_character_friction": "Three",
                  "concrete_ending_hook": {"trigger": "Three", "visible_object_or_actor": "Three", "next_problem": "Three"},
                  "chapter_mode": "investigation",
                  "civilian_life_detail": "Three",
                  "emotional_reveal": "Three",
                  "ideology_pressure": "Three"
                },
                {
                  "chapter_number": 4,
                  "act": "Act II",
                  "title": "Four",
                  "objective": "Four",
                  "conflict_turn": "Four",
                  "character_turn": "Four",
                  "reveal": "Four",
                  "ending_state": "Four",
                  "outcome_type": "setback",
                  "primary_obstacle": "Four",
                  "cost_if_success": "Four",
                  "side_character_friction": "Four",
                  "concrete_ending_hook": {"trigger": "Four", "visible_object_or_actor": "Four", "next_problem": "Four"},
                  "chapter_mode": "public_debate",
                  "civilian_life_detail": "Four",
                  "emotional_reveal": "Four",
                  "ideology_pressure": "Four"
                }
              ]
            }
            """,
            requested_chapters=4,
        )
    except ValueError as exc:
        assert "previous 2 chapters" in str(exc)
    else:
        raise AssertionError("Expected the outline parser to reject repeated recent chapter modes.")


def test_chapter_plan_parser_requires_complete_story_turn() -> None:
    try:
        parse_chapter_plan(
            """
            {
              "opening_state": "Iris hides in the maintenance shaft.",
              "character_goal": "Reach Tarin.",
              "scene_beats": ["Iris climbs", "Tarin blocks her"],
              "conflict_turn": "Tarin refuses the shortcut.",
              "ending_hook": "The door opens."
            }
            """
        )
    except ValueError as exc:
        assert "complete story_turn" in str(exc)
    else:
        raise AssertionError("Expected chapter plans to require a complete story_turn.")


def test_chapter_plan_critique_and_continuity_parsers_accept_richer_shapes() -> None:
    plan = parse_chapter_plan(
        """
        {
          "opening_state": "Iris hides in the maintenance shaft with the map pulsing in her bag.",
          "character_goal": "Get the source shard to Tarin without triggering the drones.",
          "scene_beats": ["Iris escapes the archive", "Tarin blocks her route", "The drones triangulate their comms", "Iris burns her badge to misdirect them"],
          "conflict_turn": "The drones pivot toward Tarin instead of Iris.",
          "ending_hook": "The elevator below the shaft wakes up.",
          "attempt": "Iris spoofs the drones using her archive badge.",
          "complication": "The spoof reveals Tarin's location.",
          "price_paid": "Iris permanently burns her badge and loses archive access.",
          "partial_failure_mode": "The drones still isolate Tarin's sector.",
          "ending_hook_delivery": "End on the elevator opening beneath Tarin.",
          "emotional_anchor": "Iris feels the cost of losing her archive identity.",
          "civilian_texture": "A worker passes a tea tin through the vent to Tarin.",
          "ideology_clash": "Tarin argues that survival without consent is still surrender.",
          "primary_interpersonal_conflict": "Tarin accuses Iris of treating people like systems.",
          "independent_side_character_move": "Tarin blocks the elevator route until Iris gives him the source shard.",
          "genre_specific_focus": "Keep the clue chain fair and visible.",
          "genre_specific_beats": ["Iris misreads a planted clue", "Tarin notices the missing map seam"],
          "story_turn": {
            "irreversible_change": "Iris burns her archive badge and can no longer return through official doors.",
            "protagonist_choice": "Iris chooses to burn the badge to draw drones away from Tarin.",
            "choice_alternatives": ["Iris could keep the badge and leave Tarin exposed."],
            "permanent_consequence": "The archive records Iris as a hostile intruder.",
            "why_this_chapter_cannot_be_cut": "Without this chapter, Iris never loses official access or proves she will sacrifice status for Tarin.",
            "state_before": "Iris can still pass as an archivist.",
            "state_after": "Iris is locked out and Tarin knows she chose him over the archive."
          }
        }
        """
    )
    critique = parse_chapter_critique(
        """
        {
          "strengths": ["The chapter escalates cleanly."],
          "warnings": ["The final paragraph still sounds too abstract."],
          "revision_required": true,
          "focus": ["Rewrite the ending beat around the elevator doors opening."],
          "ending_hook_type": "outline summary",
          "forward_motion_score": 8,
          "ending_concreteness_score": 4,
          "scene_turn_resolution_score": 5,
          "cost_consequence_realism_score": 7,
          "side_character_independence_score": 6,
          "proper_noun_continuity_score": 8,
          "repetition_risk_score": 3,
          "emotional_depth_score": 7,
          "ideology_clarity_score": 8,
          "civilian_texture_score": 6,
          "genre_contract_score": 7,
          "style_alignment_score": 6,
          "voice_distinctness_score": 5,
          "sentence_rhythm_score": 7,
          "sensory_specificity_score": 8,
          "dialogue_tension_score": 4,
          "technical_escalation_fatigue_score": 7,
          "irreversibility_score": 5,
          "choice_clarity_score": 6,
          "cuttable_chapter_risk_score": 4,
          "blocking_issues": ["The ending does not land on the planned object/action beat."],
          "soft_warnings": ["Tarin could resist harder in scene two."],
          "genre_contract_findings": ["The chapter plants one clue but needs a cleaner deduction turn."],
          "repair_scope": "targeted_scene_and_ending"
        }
        """
    )
    continuity = parse_continuity_update(
        """
        {
          "chapter_outcome": "Iris escapes but loses archive access.",
          "current_patch_status": "The map remains hidden but active.",
          "character_states": {"Iris": "Cut off from official systems.", "Tarin": "Now exposed to the drones."},
          "world_state": "Archive security is on full alert.",
          "open_threads": ["Who taught the map Iris's name?", "Can Tarin survive the sweep?"],
          "resolved_threads": ["Iris gets out of the archive."],
          "timeline_entry": "Iris burns her badge and escapes through the shaft.",
          "timeline": ["Iris discovers the map.", "Iris burns her badge and escapes through the shaft."],
          "new_entities_introduced": [{"name": "Shaft Elevator", "kind": "artifact", "role": "Emergency lift", "aliases": ["maintenance elevator"]}],
          "entity_state_changes": {"Living Map": "It actively responds to Iris.", "Archive Security": "Now fully mobilized."},
          "open_promises_by_name": {"map_name_source": "The map knows Iris's identity.", "tarin_exposed": "Tarin may be captured next chapter."},
          "ideology_state_by_character": {"Iris": "Freedom still matters more than forced calm.", "Tarin": "Order is acceptable only if chosen."},
          "ideology_shift_notes": {"Tarin": "Intentional hardening after the drone sweep."},
          "memory_damage": {"Iris": "She loses the smell-memory of rain after decryption."},
          "trust_fractures": {"Iris/Tarin": "Tarin no longer trusts Iris to weigh collateral costs."},
          "civilian_pressure_points": ["Families in the shelter lose archive access and heating."],
          "emotional_open_loops": {"Iris": "She fears she is choosing freedom with a damaged self."},
          "side_character_decisions": {"Tarin": ["Tarin blocks the elevator route until Iris gives him the source shard."]},
          "system_state_transitions": [
            {
              "system_name": "Living Map",
              "previous_state": "Hidden but active.",
              "new_state": "Actively responding to Iris.",
              "cause": "Iris burns her badge and speaks the map's buried name.",
              "chapter_number": 1
            }
          ],
          "story_turn": {
            "irreversible_change": "Iris burns her badge and loses archive access.",
            "protagonist_choice": "Iris chooses to protect Tarin instead of preserving her credentials.",
            "choice_alternatives": ["Iris could keep her badge and leave Tarin exposed."],
            "permanent_consequence": "Archive Security treats Iris as an intruder.",
            "why_this_chapter_cannot_be_cut": "The manuscript needs Iris to lose institutional access before the undercity chase.",
            "state_before": "Iris still has official access.",
            "state_after": "Iris is cut off from the archive."
          },
          "genre_state": {"clue_chain": "The first planted clue has been misread."}
        }
        """
    )

    assert plan.price_paid.startswith("Iris permanently burns")
    assert critique.repair_scope == "targeted_scene_and_ending"
    assert critique.ending_hook_type == "outline_summary"
    assert critique.scene_turn_resolution_score == 5
    assert continuity.new_entities_introduced[0].name == "Shaft Elevator"
    assert plan.ideology_clash.startswith("Tarin argues")
    assert critique.ideology_clarity_score == 8
    assert continuity.memory_damage["Iris"].startswith("She loses")
    assert plan.genre_specific_focus.startswith("Keep the clue chain")
    assert plan.story_turn.irreversible_change.startswith("Iris burns")
    assert plan.independent_side_character_move.startswith("Tarin blocks")
    assert critique.genre_contract_score == 7
    assert critique.voice_distinctness_score == 5
    assert critique.dialogue_tension_score == 4
    assert critique.technical_escalation_fatigue_score == 7
    assert critique.irreversibility_score == 5
    assert critique.choice_clarity_score == 6
    assert critique.cuttable_chapter_risk_score == 4
    assert continuity.genre_state["clue_chain"].startswith("The first planted clue")
    assert continuity.story_turn.permanent_consequence.startswith("Archive Security")
    assert continuity.side_character_decisions["Tarin"][0].startswith("Tarin blocks")
    assert continuity.system_state_transitions[0].system_name == "Living Map"
    assert continuity.system_state_transitions[0].new_state.startswith("Actively responding")


def test_chapter_critique_parser_normalizes_percentage_style_scores() -> None:
    critique = parse_chapter_critique(
        """
        {
          "strengths": ["The chapter escalates cleanly."],
          "warnings": [],
          "revision_required": false,
          "focus": [],
          "ending_hook_type": "image beat",
          "forward_motion_score": 70,
          "ending_concreteness_score": 40,
          "scene_turn_resolution_score": 50,
          "cost_consequence_realism_score": 65,
          "side_character_independence_score": 55,
          "proper_noun_continuity_score": 80,
          "repetition_risk_score": 30,
          "emotional_depth_score": 60,
          "ideology_clarity_score": 75,
          "civilian_texture_score": 45,
          "style_alignment_score": 80,
          "voice_distinctness_score": 55,
          "sentence_rhythm_score": 65,
          "sensory_specificity_score": 70,
          "dialogue_tension_score": 35,
          "technical_escalation_fatigue_score": 75,
          "blocking_issues": [],
          "soft_warnings": [],
          "repair_scope": "none"
        }
        """
    )

    assert critique.forward_motion_score == 7
    assert critique.ending_concreteness_score == 4
    assert critique.ending_hook_type == "image_or_feeling_beat"
    assert critique.scene_turn_resolution_score == 5
    assert critique.cost_consequence_realism_score == 7
    assert critique.side_character_independence_score == 6
    assert critique.proper_noun_continuity_score == 8
    assert critique.repetition_risk_score == 3
    assert critique.emotional_depth_score == 6
    assert critique.ideology_clarity_score == 8
    assert critique.civilian_texture_score == 5
    assert critique.style_alignment_score == 8
    assert critique.voice_distinctness_score == 6
    assert critique.sentence_rhythm_score == 7
    assert critique.sensory_specificity_score == 7
    assert critique.dialogue_tension_score == 4
    assert critique.technical_escalation_fatigue_score == 8


def test_manuscript_qa_parser_coerces_scalar_note_fields() -> None:
    report = parse_manuscript_qa_report(
        """
        {
          "overall_verdict": "The manuscript is coherent enough to export.",
          "warnings": "The middle needs one cleaner physical escalation.",
          "crisis_loop_findings": "Chapters 1, 2 repeat access -> warning -> lockout.",
          "continuity_bible_findings": "Mara changes pronouns without a logged canon fix.",
          "continuity_bible_table": [
            {
              "item_type": "character",
              "name": "Mara",
              "canon_status": "Engineer",
              "observed_status": "Pronouns shift from she/her to he/him.",
              "notes": "Needs canon decision."
            }
          ],
          "genre_contract_notes": "The selected sci-fi thriller contract is present, but the ending promise needs sharper pressure."
        }
        """
    )

    assert report.warnings == ["The middle needs one cleaner physical escalation."]
    assert report.crisis_loop_findings == ["Chapters 1, 2 repeat access -> warning -> lockout."]
    assert report.continuity_bible_findings == ["Mara changes pronouns without a logged canon fix."]
    assert report.continuity_bible_table[0].name == "Mara"
    assert report.genre_contract_notes == [
        "The selected sci-fi thriller contract is present, but the ending promise needs sharper pressure."
    ]


def test_manuscript_qa_report_preserves_continuity_bible_row_instances() -> None:
    row = ContinuityBibleRow(
        item_type="system",
        name="Living Map",
        canon_status="Dormant guide lattice",
        observed_status="Actively responding to Iris",
        notes="1 structured transition recorded.",
    )

    report = ManuscriptQaReport.model_validate({"continuity_bible_table": [row]})

    assert report.continuity_bible_table[0].item_type == "system"
    assert report.continuity_bible_table[0].name == "Living Map"
    assert report.continuity_bible_table[0].canon_status == "Dormant guide lattice"
    assert report.continuity_bible_table[0].observed_status == "Actively responding to Iris"
    assert report.continuity_bible_table[0].notes == "1 structured transition recorded."


def test_manuscript_qa_prompt_requests_crisis_loop_findings() -> None:
    project = Project(
        title="The Glass Orchard",
        premise="An archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=1,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_model="test-model",
        story_brief={},
    )
    chapter = ChapterDraft(
        chapter_number=1,
        title="Signal",
        outline_summary="Iris follows the map.",
        content="Iris enters a code. A warning starts a lockdown.",
        summary="Iris trips a warning loop.",
        continuity_update={"system_state_transitions": []},
        status=ChapterStatus.COMPLETED,
    )

    prompt = build_manuscript_qa_messages(project, {"logline": "A map wakes."}, ["Repeated lockout."], [chapter])[-1]["content"]

    assert '"crisis_loop_findings"' in prompt
    assert '"continuity_bible_findings"' in prompt
    assert '"continuity_bible_table"' in prompt
    assert '"system_state_transitions"' in prompt
    assert "representative phrases" in prompt
    assert "severity" in prompt
    assert "suggested structural fixes" in prompt
    assert "suggested renames" in prompt
    assert "unexplained core-system state transitions" in prompt


def test_continuity_update_prompt_requests_system_state_transitions() -> None:
    project = Project(
        title="The Glass Orchard",
        premise="An archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=1,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_model="test-model",
        story_brief={},
    )
    chapter = ChapterDraft(
        chapter_number=1,
        title="Signal",
        outline_summary="Iris follows the map.",
        summary="Iris wakes the Living Map.",
        status=ChapterStatus.COMPLETED,
    )
    story_bible = {
        "logline": "A map wakes.",
        "genre_profile": "sci_fi_thriller",
        "canon_registry": [
            {"name": "Living Map", "kind": "system", "role": "Dormant guide lattice", "aliases": ["the map"]}
        ],
    }
    ledger = {
        "open_threads": ["Who built the map?"],
        "active_entities": story_bible["canon_registry"],
        "system_state_by_name": {"Living Map": "Dormant guide lattice"},
    }

    prompt = build_continuity_update_messages(project, chapter, ledger, story_bible)[-1]["content"]

    assert '"system_state_transitions"' in prompt
    assert '"previous_state"' in prompt
    assert "previous_state matching the current ledger" in prompt


def test_developmental_rewrite_parser_accepts_wrapped_plan() -> None:
    plan = parse_developmental_rewrite_plan(
        """
        {
          "developmental_rewrite_plan": {
            "overall_diagnosis": "The middle repeats the same alarm loop.",
            "act_structure_notes": "Act II needs a non-technical consequence.",
            "chapter_actions": [
              {
                "chapter_numbers": "1, 2",
                "action": "merge",
                "reason": "Both chapters perform the same access-and-lockout beat.",
                "required_story_change": "Collapse the duplicated breach into one civilian-facing reversal.",
                "permanent_consequence": "Iris loses shelter access."
              }
            ],
            "merge_candidates": ["Chapters 1-2"],
            "cut_candidates": [],
            "continuity_repairs": [],
            "theme_arc_repairs": [],
            "prose_pattern_repairs": ["Remove repeated future-hangs phrasing."],
            "pre_rewrite_risks": ["Repeated alarm loop."],
            "post_rewrite_risk_targets": ["One merged chapter creates a unique consequence."]
          }
        }
        """
    )

    assert plan.chapter_actions[0].chapter_numbers == [1, 2]
    assert plan.chapter_actions[0].action == "merge"
    assert plan.act_structure_notes == ["Act II needs a non-technical consequence."]
    assert plan.post_rewrite_risk_targets == ["One merged chapter creates a unique consequence."]


def test_developmental_rewrite_prompt_includes_full_manuscript_and_qa() -> None:
    project = Project(
        title="The Glass Orchard",
        premise="An archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=1,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_model="test-model",
        story_brief={},
    )
    chapter = ChapterDraft(
        chapter_number=1,
        title="Signal",
        outline_summary="Iris follows the map.",
        content="Chapter 1: Signal\n\nIris follows the map. Tarin refuses the easy route.",
        summary="Iris follows the map.",
        qa_notes={"repetition_risk_score": 7},
    )
    chapter.continuity_update = {
        "story_turn": {
            "irreversible_change": "Iris burns the archive key.",
            "protagonist_choice": "Iris chooses entry over permission.",
            "permanent_consequence": "The lower stair stays open.",
        }
    }
    qa_report = parse_manuscript_qa_report(
        """
        {
          "overall_verdict": "The manuscript is coherent but repetitive.",
          "repetition_risks": ["The first act repeats access-and-warning beats."],
          "crisis_loop_findings": ["Chapters 1, 2 repeat crisis-loop pattern: access/log operation -> alarm/warning activation."]
        }
        """
    )

    messages = build_developmental_rewrite_messages(
        project,
        {"logline": "A map wakes.", "genre_profile": "sci_fi_thriller"},
        {"current_patch_status": "No repair yet."},
        qa_report,
        [chapter],
    )
    prompt = messages[-1]["content"]

    assert "Full manuscript chapters" in prompt
    assert "Iris follows the map. Tarin refuses the easy route." in prompt
    assert "crisis_loop_findings" in prompt
    assert "access/log operation -> alarm/warning activation" in prompt
    assert "pre_rewrite_risks" in prompt
    assert "post_rewrite_risk_targets" in prompt


def test_prompt_builders_include_prose_voice_profile() -> None:
    project = Project(
        title="The Glass Orchard",
        premise="An archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=1,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_model="test-model",
        story_brief={
            "tone": "tense luminous sci-fi",
            "style_targets": ["taut lyric pressure", "concrete sensory dread"],
            "dialogue_targets": ["arguments with subtext"],
            "style_avoid": ["weight of everything"],
            "style_reference": "Short clipped sentences. Wet stone. No copied lines.",
        },
    )
    run = GenerationRun(
        model_name="test-model",
        target_word_count=2000,
        requested_chapters=1,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
    )
    chapter = ChapterDraft(
        chapter_number=1,
        title="Signal",
        outline_summary="Iris follows the map.",
        content="Iris follows the map. Tarin refuses the easy route.",
        status=ChapterStatus.PENDING,
    )
    story_bible = {
        "genre_profile": "sci_fi_thriller",
        "logline": "Iris follows a living map.",
        "theme": "Consent beats control.",
        "act_plan": ["Discovery", "Descent", "Choice"],
        "cast": [{"name": "Iris", "role": "Archivist", "desire": "Restore the city", "risk": "Losing herself"}],
        "character_agendas": [],
        "canon_registry": [],
        "conflict_ladder": ["Map wakes"],
        "world_rules": [],
        "core_system_rules": [],
        "prose_guardrails": [],
        "genre_contract": [],
        "style_profile": {
            "narrative_voice": "Close third, tense and tactile.",
            "sentence_rhythm": "Short pressure beats with occasional long sensory release.",
            "imagery_palette": ["wet stone", "blue archive light"],
            "dialogue_rules": ["Each exchange contains disagreement or withheld context."],
            "character_voice_map": {"Iris": "precise when afraid", "Tarin": "plainspoken refusal"},
            "avoid": ["weight of everything"],
        },
        "ending_promise": "Iris chooses consent.",
    }
    outline_entry = {
        "chapter_number": 1,
        "act": "Act I",
        "title": "Signal",
        "objective": "Follow the map.",
        "conflict_turn": "Tarin blocks the easy route.",
        "character_turn": "Iris accepts help has a price.",
        "reveal": "The map knows her name.",
        "ending_state": "The route opens below them.",
        "outcome_type": "reversal",
        "primary_obstacle": "Archive lockdown",
        "cost_if_success": "Iris burns access",
        "side_character_friction": "Tarin refuses to trust the map.",
        "independent_side_character_move": "Tarin blocks the route until Iris admits the map may be manipulating her.",
        "concrete_ending_hook": {"trigger": "A door opens", "visible_object_or_actor": "blue lens", "next_problem": "the route descends"},
        "chapter_mode": "systems_crisis",
        "civilian_life_detail": "Workers trade heat tabs.",
        "emotional_reveal": "Iris fears obedience.",
        "ideology_pressure": "Tarin challenges control.",
    }
    plan = {
        "opening_state": "Iris hides with the map.",
        "character_goal": "Reach the source.",
        "scene_beats": ["Iris climbs", "Tarin refuses", "The door opens"],
        "conflict_turn": "Tarin blocks her.",
        "ending_hook": "The door opens.",
        "independent_side_character_move": "Tarin blocks the route until Iris admits the map may be manipulating her.",
        "story_turn": {
            "irreversible_change": "Iris gives Tarin the source route and loses unilateral control of the map.",
            "protagonist_choice": "Iris chooses to admit the map may be manipulating her.",
            "choice_alternatives": ["Iris could keep the route secret and force Tarin to follow."],
            "permanent_consequence": "Tarin now has leverage over the undercity route.",
            "why_this_chapter_cannot_be_cut": "Without it, Iris never gives up control or lets Tarin alter the mission.",
            "state_before": "Iris controls the route alone.",
            "state_after": "Tarin can block or redirect the mission.",
        },
    }
    ledger = {
        "current_patch_status": "Unknown.",
        "character_states": {},
        "world_state": "Archive locked.",
        "open_threads": [],
        "resolved_threads": [],
        "timeline": [],
        "active_entities": [],
        "entity_state_changes": {},
        "open_promises_by_name": {},
        "ideology_state_by_character": {},
        "memory_damage": {},
        "trust_fractures": {},
        "civilian_pressure_points": [],
        "emotional_open_loops": {},
        "side_character_decisions": {},
        "genre_state": {"recent_chapter_modes": "Chapter 1: investigation; Chapter 2: aftermath"},
    }
    critique = parse_chapter_critique(
        """
        {
          "strengths": [],
          "warnings": [],
          "revision_required": true,
          "focus": ["Strengthen style profile alignment."],
          "ending_hook_type": "abstract_cliffhanger",
          "forward_motion_score": 8,
          "ending_concreteness_score": 8,
          "scene_turn_resolution_score": 4,
          "cost_consequence_realism_score": 8,
          "side_character_independence_score": 8,
          "proper_noun_continuity_score": 8,
          "repetition_risk_score": 3,
          "emotional_depth_score": 8,
          "ideology_clarity_score": 8,
          "civilian_texture_score": 8,
          "style_alignment_score": 4,
          "voice_distinctness_score": 5,
          "sentence_rhythm_score": 4,
          "sensory_specificity_score": 5,
          "dialogue_tension_score": 4,
          "technical_escalation_fatigue_score": 8,
          "blocking_issues": [],
          "soft_warnings": ["The prose is too generic."],
          "repair_scope": "voice_and_texture"
        }
        """
    )

    story_prompt = build_story_bible_messages(project, run)[1]["content"]
    plan_prompt = build_chapter_plan_messages(project, run, chapter, outline_entry, story_bible, ledger, "No previous chapters.")[1]["content"]
    draft_prompt = build_chapter_draft_messages(project, run, chapter, outline_entry, story_bible, ledger, "No previous chapters.", plan)[1]["content"]
    critique_prompt = build_chapter_critique_messages(project, chapter, outline_entry, story_bible, ledger, plan, [])[1]["content"]
    revision_prompt = build_chapter_revision_messages(project, chapter, outline_entry, story_bible, ledger, plan, critique, [])[1]["content"]

    assert "Style targets: taut lyric pressure" in story_prompt
    assert '"style_profile"' in story_prompt
    assert "Recent chapter modes to avoid repeating" in plan_prompt
    assert "previous 2 chapters" in plan_prompt
    assert "chapter_mode" in plan_prompt
    assert "irreversible_change" in plan_prompt
    assert "why_this_chapter_cannot_be_cut" in plan_prompt
    assert "Prose style profile" in draft_prompt
    assert "character_voice_map" in draft_prompt
    assert "final paragraph must include" in draft_prompt
    assert "visible consequence" in draft_prompt
    assert "at most one primary system-crisis mechanic" in draft_prompt
    assert "human-visible consequences" in draft_prompt
    assert "independent_side_character_move" in draft_prompt
    assert "chapter_plan.story_turn" in draft_prompt
    assert "side characters who appear must pursue their own want" in draft_prompt
    assert "style_alignment_score" in critique_prompt
    assert "ending_hook_type" in critique_prompt
    assert "scene_turn_resolution_score" in critique_prompt
    assert "technical_escalation_fatigue_score" in critique_prompt
    assert "irreversibility_score" in critique_prompt
    assert "choice_clarity_score" in critique_prompt
    assert "cuttable_chapter_risk_score" in critique_prompt
    assert "abstract_cliffhanger" in critique_prompt
    assert "next problem" in critique_prompt
    assert "meta/outlining language" in critique_prompt
    assert "lockdowns, quarantines, reboots, alarms" in critique_prompt
    assert "side_character_independence_score should be 5 or lower" in critique_prompt
    assert "voice_and_texture" in critique_prompt
    assert "concrete external action" in revision_prompt
    assert "unique protagonist choice and one permanent consequence" in revision_prompt
    assert "remove meta/outlining language" in revision_prompt
    assert "remove repeated alarm-console escalation" in revision_prompt
    assert "add or sharpen the planned independent_side_character_move" in revision_prompt
    assert "voice_and_texture" in revision_prompt
    assert "do not copy exact language" in revision_prompt


def test_rolling_context_uses_recent_completed_chapters() -> None:
    first = ChapterDraft(
        chapter_number=1,
        title="Arrival",
        outline_summary="Wake the map.",
        summary="The archivist finds the map.",
        status=ChapterStatus.COMPLETED,
    )
    second = ChapterDraft(
        chapter_number=2,
        title="Descent",
        outline_summary="Enter the undercity.",
        summary="The descent reveals sentient tunnels.",
        status=ChapterStatus.COMPLETED,
    )
    third = ChapterDraft(
        chapter_number=3,
        title="Ashes",
        outline_summary="Face the caretaker.",
        summary="A caretaker offers a dangerous bargain.",
        status=ChapterStatus.COMPLETED,
    )

    context = rolling_context([first, second, third], window=2)

    assert "Chapter 2" in context
    assert "Chapter 3" in context
    assert "Chapter 1" not in context
