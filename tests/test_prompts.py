from __future__ import annotations

from novel_generator.models import ChapterDraft, ChapterStatus
from novel_generator.services.prompts import (
    parse_chapter_critique,
    parse_chapter_plan,
    parse_continuity_update,
    parse_outline,
    parse_story_bible,
    rolling_context,
    sanitize_chapter_content,
)


def test_story_bible_parser_accepts_valid_json_with_fences() -> None:
    parsed = parse_story_bible(
        """```json
        {
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
              "relationship_to_protagonist": "Self"
            }
          ],
          "canon_registry": [
            {"name": "Living Map", "kind": "system", "role": "Sentient map", "aliases": ["Map"]}
          ],
          "conflict_ladder": ["The map awakens", "The city fights back", "Iris must choose who controls memory"],
          "world_rules": ["The city records memory in living stone."],
          "core_system_rules": ["Maps can rewrite routes and memories."],
          "prose_guardrails": ["No abstract ending thesis statements."],
          "ending_promise": "The city survives only if Iris gives up control."
        }
        ```"""
    )

    assert parsed.logline.startswith("An archivist")
    assert parsed.cast[0].name == "Iris"
    assert parsed.character_agendas[0].line_in_sand.startswith("She will not erase")
    assert parsed.canon_registry[0].name == "Living Map"
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
              }
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
              }
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
                  }
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
            }
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
              }
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
              }
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
          "ending_hook_delivery": "End on the elevator opening beneath Tarin."
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
          "forward_motion_score": 8,
          "ending_concreteness_score": 4,
          "cost_consequence_realism_score": 7,
          "side_character_independence_score": 6,
          "proper_noun_continuity_score": 8,
          "repetition_risk_score": 3,
          "blocking_issues": ["The ending does not land on the planned object/action beat."],
          "soft_warnings": ["Tarin could resist harder in scene two."],
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
          "open_promises_by_name": {"map_name_source": "The map knows Iris's identity.", "tarin_exposed": "Tarin may be captured next chapter."}
        }
        """
    )

    assert plan.price_paid.startswith("Iris permanently burns")
    assert critique.repair_scope == "targeted_scene_and_ending"
    assert continuity.new_entities_introduced[0].name == "Shaft Elevator"


def test_chapter_critique_parser_normalizes_percentage_style_scores() -> None:
    critique = parse_chapter_critique(
        """
        {
          "strengths": ["The chapter escalates cleanly."],
          "warnings": [],
          "revision_required": false,
          "focus": [],
          "forward_motion_score": 70,
          "ending_concreteness_score": 40,
          "cost_consequence_realism_score": 65,
          "side_character_independence_score": 55,
          "proper_noun_continuity_score": 80,
          "repetition_risk_score": 30,
          "blocking_issues": [],
          "soft_warnings": [],
          "repair_scope": "none"
        }
        """
    )

    assert critique.forward_motion_score == 7
    assert critique.ending_concreteness_score == 4
    assert critique.cost_consequence_realism_score == 7
    assert critique.side_character_independence_score == 6
    assert critique.proper_noun_continuity_score == 8
    assert critique.repetition_risk_score == 3


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
