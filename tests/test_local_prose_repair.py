from types import SimpleNamespace

import pytest

from novel_generator.services.local_prose_repair import apply_repair, repair_span, repair_plan, repair_passages


def issue(evidence, category="repetition"):
    return SimpleNamespace(evidence=evidence, category=category)


def test_local_edit_preserves_surrounding_prose_byte_for_byte():
    text = "Opening stays.\r\n\r\nHe spoke.  He spoke.\r\n\r\nEnding stays."
    span = repair_span(text, [issue("He spoke. He spoke.")])
    assert apply_repair(text, span, "He spoke.") == "Opening stays.\r\n\r\nHe spoke.\r\n\r\nEnding stays."


def test_anchor_expands_to_complete_paragraphs():
    text = "First.\n\nShe said yes.\n\nShe said yes again.\n\nLast."
    span = repair_span(text, [issue("yes. She said")])
    assert text[slice(*span)] == "She said yes.\n\nShe said yes again."


@pytest.mark.parametrize("evidence,category", [
    ("not in source", "prose"), ("said", "prose"),
    ("She said yes.", "causality"), ("She said ... again.", "repetition"),
])
def test_ambiguous_invented_or_nonlocal_diagnosis_uses_full_repair(evidence, category):
    assert repair_span("She said yes. She said yes again.", [issue(evidence, category)]) is None


def test_large_passage_or_distant_defects_use_full_repair():
    assert repair_span("word " * 251, [issue("word word")]) is None
    text = "One.\n\n" + "word " * 251 + "\n\nTwo."
    assert repair_span(text, [issue("One."), issue("Two.")]) is None


def test_nearby_or_duplicate_diagnoses_share_one_bounded_passage():
    text = "Start.\n\nOne.\n\nTwo.\n\nEnd."
    span = repair_span(text, [issue("One."), issue("Two."), issue("One.")])
    assert text[slice(*span)] == "One.\n\nTwo."


@pytest.mark.parametrize("replacement", ["", "Original.", "word " * 31])
def test_unusable_local_output_is_rejected(replacement):
    with pytest.raises(ValueError):
        apply_repair("Original.", (0, 9), replacement)


def test_distant_edits_preserve_intervening_prose_and_merge_overlapping_reports():
    first = "She waited. She waited."
    last = "He left. He left."
    middle = "\r\n\r\n" + "Untouched story. " * 150 + "\r\n\r\n"
    text = first + middle + last
    plan = repair_plan(text, [issue(first), issue(last), issue(first)])
    assert len(plan) == 2
    # The helper's prompt serialization requires real contracts in production.
    for _, diagnoses in plan:
        for diagnosis in diagnoses:
            diagnosis.model_dump = lambda: {}
    results = iter(["He left.", "She waited."])
    assert repair_passages(text, plan, lambda *args: next(results)) == "She waited." + middle + "He left."


def test_local_plan_rejects_ungrounded_issue_or_too_many_passages():
    assert repair_plan("One.", [issue("missing")]) is None
    paragraphs = [f"Passage {number}." for number in range(9)]
    assert repair_plan("\n\n".join(paragraphs), [issue(p) for p in paragraphs]) is None
