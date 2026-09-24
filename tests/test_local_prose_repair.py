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


def test_repeated_quote_edits_each_occurrence_without_rewriting_middle():
    text = "She said, 'You do not understand.'\n\nThe gate remained locked.\n\nHe said, 'You do not understand.'"
    diagnosis = issue("You do not understand.")
    diagnosis.model_dump = lambda: {}
    plan = repair_plan(text, [diagnosis])
    assert len(plan) == 2
    replies = iter(["He said, 'The mechanism is more complex than that.'", "She said, 'You do not understand.'"])
    candidate = repair_passages(text, plan, lambda *args: next(replies))
    assert candidate == "She said, 'You do not understand.'\n\nThe gate remained locked.\n\nHe said, 'The mechanism is more complex than that.'"


def test_numbered_evidence_repairs_separate_paragraphs_without_omission_matching():
    text = "First speech.\n\nUntouched action.\n\nSecond speech."
    diagnosis = issue("First speech. [...] Second speech.")
    diagnosis.evidence_paragraphs = [1, 3]
    plan = repair_plan(text, [diagnosis])
    assert [text[slice(*span)] for span, _ in plan] == ["First speech.", "Second speech."]


def test_causal_edit_preserves_prior_repairs_and_receives_context():
    import json
    text = "The gate cracked.\n\nRepaired dialogue stays.\n\nThe pump broke."
    diagnosis = issue('“The pump broke.”', 'causality')
    diagnosis.model_dump = lambda: {}
    plan = repair_plan(text, [diagnosis])
    def generate(messages, *args):
        request = json.loads(messages[1]['content'])
        assert request['read_only_context'] == {'actual_prose': text}
        assert request['passage_to_repair'] == 'The pump broke.'
        return 'The gate’s vibration fractured the pump.'
    assert repair_passages(text, plan, generate, {'actual_prose': text}) == (
        'The gate cracked.\n\nRepaired dialogue stays.\n\nThe gate’s vibration fractured the pump.')


def test_ellipsis_quotes_map_only_grounded_passages():
    text = 'She said, “Stay here.”\n\nUnchanged.\n\nHe said, “Stay here.”'
    diagnosis = issue('She said, "Stay here." ... He said, "Stay here."')
    assert len(repair_plan(text, [diagnosis])) == 2
    diagnosis.evidence += ' ... Invented ending.'
    assert repair_plan(text, [diagnosis]) is None


def test_oversized_response_is_corrected_without_changing_surroundings():
    text = 'Opening.\n\nThe pump broke.\n\nEnding.'
    diagnosis = issue('The pump broke.', 'causality')
    diagnosis.model_dump = lambda: {}
    responses = iter(['word ' * 100, 'Vibration from the gate broke the pump.'])
    calls = []
    def generate(messages, *args):
        calls.append(messages)
        return next(responses)
    assert repair_passages(text, repair_plan(text, [diagnosis]), generate) == (
        'Opening.\n\nVibration from the gate broke the pump.\n\nEnding.')
    assert '1-30 words' in calls[1][-1]['content']


def test_invalid_passage_retries_are_bounded():
    diagnosis = issue('The pump broke.', 'causality')
    diagnosis.model_dump = lambda: {}
    calls = []
    def generate(*args):
        calls.append(1)
        return 'word ' * 100
    with pytest.raises(ValueError, match='expanded'):
        repair_passages('The pump broke.', repair_plan('The pump broke.', [diagnosis]), generate)
    assert len(calls) == 3
