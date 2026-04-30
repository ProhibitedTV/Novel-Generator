from __future__ import annotations

from novel_generator.models import ChapterDraft, ChapterStatus
from novel_generator.services.prompts import (
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
          "world_rules": ["The city records memory in living stone."],
          "core_system_rules": ["Maps can rewrite routes and memories."],
          "ending_promise": "The city survives only if Iris gives up control."
        }
        ```"""
    )

    assert parsed.logline.startswith("An archivist")
    assert parsed.cast[0].name == "Iris"
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
              "ending_state": "Iris escapes with proof the map is alive."
            },
            {
              "chapter_number": 2,
              "act": "Act I",
              "title": "Descent",
              "objective": "Trace the map beneath the city.",
              "conflict_turn": "The tunnels begin rewriting the route.",
              "character_turn": "Iris chooses trust over isolation.",
              "reveal": "The city has been steering her for years.",
              "ending_state": "Iris commits to the undercity mission."
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
                  "ending_state": "The mission begins."
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


def test_sanitize_chapter_content_removes_duplicate_heading() -> None:
    cleaned = sanitize_chapter_content("Chapter 7: Descent\n\nThe real prose starts here.")

    assert cleaned == "The real prose starts here."


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
