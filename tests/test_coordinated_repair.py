import json
from types import SimpleNamespace

import pytest

from novel_generator.services.coordinated_repair import repair
from novel_generator.services.local_prose_repair import repair_plan


def setup():
    text = 'The pump broke.\n\nUnchanged dialogue.\n\nThe pump broke again.'
    issue = SimpleNamespace(category='causality', evidence_paragraphs=[1, 3])
    issue.model_dump = lambda: {'category': 'causality', 'problem': 'The same failure occurs twice.'}
    return text, repair_plan(text, [issue])


def test_joint_edit_deletes_duplicate_and_preserves_intervening_prose():
    text, plan = setup()
    def generate(messages, *args):
        request = json.loads(messages[1]['content'])
        assert len(request['passages']) == 2
        assert request['read_only_context']['actual_prose'] == text
        return json.dumps({'edits': [{'id': 1, 'text': 'Gate vibration broke the pump.'}, {'id': 2, 'text': ''}]})
    candidate = repair(text, plan, {'actual_prose': text}, generate)
    assert candidate == 'Gate vibration broke the pump.\n\nUnchanged dialogue.\n\n'


@pytest.mark.parametrize('edits', [
    [{'id': 1, 'text': 'Fixed.'}],
    [{'id': 1, 'text': 'Fixed.'}, {'id': 1, 'text': ''}],
    [{'id': 1, 'text': ''}, {'id': 2, 'text': ''}],
    [{'id': 1, 'text': 'word ' * 100}, {'id': 2, 'text': ''}],
])
def test_invalid_edit_sets_fail_atomically_after_bounded_retries(edits):
    text, plan = setup()
    calls = []
    def generate(*args):
        calls.append(1)
        return json.dumps({'edits': edits})
    with pytest.raises(ValueError):
        repair(text, plan, {}, generate)
    assert len(calls) == 3
    assert text.endswith('The pump broke again.')


def test_invalid_json_is_corrected_against_original_source():
    text, plan = setup()
    responses = iter(['not JSON', json.dumps({'edits': [{'id': 1, 'text': 'The gate broke the pump.'}, {'id': 2, 'text': ''}]})])
    assert 'Unchanged dialogue.' in repair(text, plan, {}, lambda *args: next(responses))
