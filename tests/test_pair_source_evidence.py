import json
from types import SimpleNamespace

import pytest

from novel_generator.services import autonomous_editorial as editor
from novel_generator.services.structured_schema_runtime import extract_schema_marker
from test_autonomous_editorial import clean_review, issue, make_run, install_fake_provider
from novel_generator.settings import get_settings
from novel_generator.schemas import ManuscriptQaReport


def chapters():
    return [SimpleNamespace(chapter_number=16, content='They prepared the tools.\n\nThe charms were already dismantled.'),
            SimpleNamespace(chapter_number=17, content='They removed the still-active charm.\n\nThe hum ceased.\n\nThey went home.')]


def conflict(references, **kwargs):
    data = {**clean_review([16, 17]), 'canon_consistent': False}
    data['issues'] = [dict(issue(number=17, evidence='Source paragraphs'),
        source_references=references, **kwargs)]
    return json.dumps(data)


def test_pair_references_attach_only_target_text_and_survive_saved_review_roundtrip():
    refs = [{'chapter_number': 17, 'paragraph_number': 2},
            {'chapter_number': 16, 'paragraph_number': 2},
            {'chapter_number': 17, 'paragraph_number': 1},
            {'chapter_number': 16, 'paragraph_number': 2}]
    review = editor.validate_review(conflict(refs), chapters())
    assert not review.passed
    diagnosis = review.issues[0]
    assert diagnosis.evidence == 'They removed the still-active charm. [...] The hum ceased.'
    assert diagnosis.evidence_paragraphs == [1, 2]
    assert [(ref.chapter_number, ref.paragraph_number) for ref in diagnosis.source_references] == [(16, 2), (17, 1), (17, 2)]
    assert editor.validate_review(review.model_dump_json(), chapters()) == review
    assert diagnosis._source_evidence[0] == dict(chapter_number=16, paragraph_number=2,
        actual_prose='The charms were already dismantled.')


@pytest.mark.parametrize('references,message', [
    ([{'chapter_number': 16, 'paragraph_number': 3}, {'chapter_number': 17, 'paragraph_number': 1}], 'outside'),
    ([{'chapter_number': 18, 'paragraph_number': 1}, {'chapter_number': 17, 'paragraph_number': 1}], 'outside'),
    ([{'chapter_number': 16, 'paragraph_number': 2}], 'selected for repair'),
    ([{'chapter_number': 17, 'paragraph_number': '1'}], 'valid integer'),
    ([16, 2, 17, 1], 'valid dictionary'),
])
def test_ambiguous_or_invalid_pair_references_never_certify_a_review(references, message):
    with pytest.raises(ValueError, match=message):
        editor.validate_review(conflict(references), chapters())


def test_conflicting_local_edit_anchors_cannot_override_explicit_source_evidence():
    with pytest.raises(ValueError, match='disagrees'):
        editor.validate_review(conflict([{'chapter_number': 16, 'paragraph_number': 2},
            {'chapter_number': 17, 'paragraph_number': 1}], evidence_paragraphs=[2]), chapters())


def test_decoder_and_independent_verifier_use_each_source_chapters_actual_bounds():
    source = {'actual_chapters': [{'chapter_number': chapter.chapter_number,
        'actual_prose': editor._numbered_prose(chapter.content, chapter.chapter_number)} for chapter in chapters()]}
    counts = editor._source_paragraph_counts(source)
    assert counts == {16: 2, 17: 3}
    _, schema, _ = extract_schema_marker([editor._coverage_marker([16, 17], 'adjacent_chapters', paragraph_counts=counts)])
    definition = schema['$defs']['EditorialIssue']
    assert 'source_references' in definition['required']
    assert definition['properties']['evidence_paragraphs']['const'] == []
    variants = definition['properties']['source_references']['items']['anyOf']
    assert [(item['properties']['chapter_number']['const'], item['properties']['paragraph_number']['enum'])
            for item in variants] == [(16, [1, 2]), (17, [1, 2, 3])]
    review = editor.validate_review(conflict([{'chapter_number': 16, 'paragraph_number': 2},
        {'chapter_number': 17, 'paragraph_number': 1}]), chapters())
    messages = editor._adjudication_messages('adjacent_chapters', source, review, {})
    _, adjudication_schema, _ = extract_schema_marker(messages)
    assert adjudication_schema == schema
    allegation = json.loads(messages[-1]['content'])['proposed_review_to_verify'][0]
    assert allegation['source_references'] == [ref.model_dump() for ref in review.issues[0].source_references]
    assert 'repair_instruction' not in allegation


def test_pair_allegation_cannot_omit_the_other_chapters_support(configured_environment, monkeypatch):
    def review(context, numbers):
        return {**clean_review(numbers), 'canon_consistent': False,
            'issues': [dict(issue(number=2, evidence='Source paragraphs'),
                source_references=[{'chapter_number': 2, 'paragraph_number': 1}])]}
    install_fake_provider(monkeypatch, review_fn=review)
    from novel_generator.dependencies import get_session_factory
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        with pytest.raises(ValueError, match='both chapters'):
            editor._review(session, run, run.chapters,
                editor._adjacent_context(run, run.chapters, None), object(), 'adjacent_chapters')
        assert not editor._events(run, 'autonomous_review_completed')
        assert not editor._events(run, 'autonomous_repair_started')


def test_confirmed_pair_defect_is_repaired_and_both_chapters_reaudited(configured_environment, monkeypatch):
    broken = 'The gate was still locked.'
    fixed = 'The gate stayed open. Ivo crossed safely.'
    calls_to_pair = []
    def review(context, numbers):
        output = clean_review(numbers)
        if context['scope'] == 'adjacent_chapters':
            calls_to_pair.append(context)
            if broken in context['actual_chapters'][1]['actual_prose']:
                output['canon_consistent'] = False
                output['issues'] = [dict(issue(number=2, evidence='Source paragraphs'),
                    source_references=[{'chapter_number': 1, 'paragraph_number': 2},
                                       {'chapter_number': 2, 'paragraph_number': 1}],
                    problem='Chapter 1 opens the gate; Chapter 2 says that same gate remains locked.',
                    repair_instruction='Keep the gate open as established in Chapter 1.')]
        return output
    calls = install_fake_provider(monkeypatch, review_fn=review, revision=fixed)
    from novel_generator.services import pipeline
    chat = pipeline._supervised_provider_chat
    repair_contexts = []
    def inspect_repair(*args, **kwargs):
        if kwargs['stage'] == 'autonomous_revision':
            payload = json.loads(args[5][-1]['content'])
            repair_contexts.append(payload.get('read_only_context', payload.get('context')))
        return chat(*args, **kwargs)
    monkeypatch.setattr(pipeline, '_supervised_provider_chat', inspect_repair)
    from novel_generator.dependencies import get_session_factory
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        first, second = run.chapters
        first.content = 'Ivo approached the river.\n\nMara opened the gate.'
        second.content = broken + ' Ivo waited.'
        first_before = first.content
        run.target_word_count = 16
        run.min_words_per_chapter = 6
        session.commit()
        editor.finish_manuscript(session, run, run.chapters, get_settings(), object(), ManuscriptQaReport())
        assert first.content == first_before
        assert second.content == fixed
        assert calls.count(('autonomous_revision', 2)) == 1
        assert len(calls_to_pair) == 3  # Auditor, independent verifier, repaired pair.
        assert repair_contexts[0]['verified_source_evidence'] == [
            dict(chapter_number=1, paragraph_number=2, actual_prose='Mara opened the gate.'),
            dict(chapter_number=2, paragraph_number=1, actual_prose=broken + ' Ivo waited.')]
        assert editor._events(run, 'autonomous_quality_passed')
