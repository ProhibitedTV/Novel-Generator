from types import SimpleNamespace

import pytest

from novel_generator.services import autonomous_editorial as editor


def chapter(number, text):
    return SimpleNamespace(chapter_number=number, content=text)


BASE = (
    "The morning wind swept through the abandoned orchard, scattering brittle leaves "
    "across the narrow stone path that led from the empty house toward the old reservoir. "
    "Sera stopped beside the broken gate and listened for the familiar footsteps of her sister "
    "before continuing toward the water with the bundle of tools she had borrowed yesterday."
)


@pytest.mark.parametrize("text", [
    "In Chapter 1, Sera had learned the truth.",
    "She retrieved the ledger from Chapter 4.",
    "The transfer in Chapter 21 remained intact.",
])
def test_numbered_chapter_reference_triggers_artifact_review(text):
    assert editor.OUTPUT_ARTIFACT_PATTERN.search(text)


@pytest.mark.parametrize("text", [
    "They turned to the next chapter of their lives.",
    "She walked toward the orchard with her sister.",
])
def test_ordinary_narration_does_not_trigger_new_pattern(text):
    assert not editor.OUTPUT_ARTIFACT_PATTERN.search(text)


def test_near_duplicate_hints_locate_nonadjacent_source_paragraphs():
    revised = BASE.replace("morning wind", "evening breeze").replace("borrowed yesterday", "found upstairs")
    chapters = [
        chapter(1, "Opening.\n\n" + BASE),
        chapter(4, "A different scene."),
        chapter(9, revised + "\n\nEnding."),
    ]
    matches = editor._near_repeated_passages(chapters)
    assert len(matches) == 1
    assert matches[0]["source_locations"] == [
        {"chapter_number": 1, "paragraph": 2},
        {"chapter_number": 9, "paragraph": 1},
    ]
    assert matches[0]["actual_excerpts"] == [BASE, revised]
    assert 0.65 <= matches[0]["similarity"] < 1
    assert chapters[0].content == "Opening.\n\n" + BASE


def test_near_duplicate_hints_also_find_intra_chapter_rewrites():
    revised = BASE.replace("abandoned orchard", "deserted garden")
    matches = editor._near_repeated_passages([chapter(3, BASE + "\n\n" + revised)])
    assert matches[0]["source_locations"] == [
        {"chapter_number": 3, "paragraph": 1},
        {"chapter_number": 3, "paragraph": 2},
    ]


def test_exact_copies_short_refrains_and_shared_topic_are_not_near_duplicates():
    unrelated = (
        "Sera waited near the reservoir for her sister. The orchard stood beyond the gate. "
        "Yesterday the tools had broken, so she borrowed a bundle from the stone house. "
        "By morning she would learn whether the water could reach the empty garden."
    )
    assert editor._near_repeated_passages([
        chapter(1, BASE + "\n\nA refrain."),
        chapter(2, BASE + "\n\nA refrain.\n\n" + unrelated),
    ]) == []
    assert editor._book_repeated_passages([chapter(1, BASE), chapter(2, BASE)])


def test_near_duplicate_hints_are_bounded_and_deterministic():
    chapters = [chapter(i, BASE.replace("morning wind", f"weather at hour {i}")) for i in range(1, 10)]
    first = editor._near_repeated_passages(chapters)
    assert len(first) == 12
    assert first == editor._near_repeated_passages(chapters)
