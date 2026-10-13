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


def test_pair_review_can_confirm_a_conflict_wholly_within_one_chapter(configured_environment, monkeypatch):
    def review(context, numbers):
        return {**clean_review(numbers), 'canon_consistent': False,
            'issues': [dict(issue(number=2, evidence='Source paragraphs'),
                source_references=[{'chapter_number': 2, 'paragraph_number': 1}])]}
    install_fake_provider(monkeypatch, review_fn=review)
    from novel_generator.dependencies import get_session_factory
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        verdict = editor._review(session, run, run.chapters,
            editor._adjacent_context(run, run.chapters, None), object(), 'adjacent_chapters')
        assert not verdict.passed
        assert verdict.issues[0].evidence == run.chapters[1].content
        assert editor._events(run, 'autonomous_review_adjudicated')
        assert editor._events(run, 'autonomous_review_completed')
        assert not editor._events(run, 'autonomous_repair_started')


def test_new_pair_inference_cannot_use_an_ambiguous_legacy_integer_array(configured_environment, monkeypatch):
    def review(context, numbers):
        return {**clean_review(numbers), 'canon_consistent': False,
            'issues': [dict(issue(number=2, evidence='Source paragraphs'), evidence_paragraphs=[1])]}
    install_fake_provider(monkeypatch, review_fn=review)
    from novel_generator.dependencies import get_session_factory
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        with pytest.raises(ValueError, match='explicit source_references'):
            editor._review(session, run, run.chapters,
                editor._adjacent_context(run, run.chapters, None), object(), 'adjacent_chapters')
        assert not editor._events(run, 'autonomous_review_completed')


def test_changed_source_defers_a_stale_repair_without_spending_its_budget(configured_environment, monkeypatch):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.services import pipeline
    calls = install_fake_provider(monkeypatch, revision='Mara opened the gate. Ivo crossed safely.')
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        first, second = run.chapters
        first.content = 'Mara opened the gate.'
        second.content = 'The gate remained locked. Ivo waited.'
        data = {**clean_review([1, 2]), 'canon_consistent': False, 'issues': [dict(issue(number=2,
            evidence='Source paragraphs'), source_references=[{'chapter_number': 1, 'paragraph_number': 1},
                                                            {'chapter_number': 2, 'paragraph_number': 1}])]}
        old_review = editor.validate_review(json.dumps(data), run.chapters)
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        first.content += ' She showed Ivo how the latch worked.'
        second_before = second.content
        session.commit()
        editor._repair(session, run, second, ledger, old_review.issues, get_settings(), object(), 'final')
        assert second.content == second_before
        assert not calls
        assert not editor._events(run, 'autonomous_repair_started')
        assert editor._events(run, 'autonomous_repair_deferred')[-1]['changed_source_chapters'] == [1]
        fresh_review = editor.validate_review(json.dumps(data), run.chapters)
        editor._repair(session, run, second, ledger, fresh_review.issues, get_settings(), object(), 'final')
        assert second.content == 'Mara opened the gate. Ivo crossed safely.'
        assert calls.count(('autonomous_revision', 2)) == 1
        assert len(editor._events(run, 'autonomous_repair_started')) == 1


def test_whole_book_cache_invalidates_when_text_outside_compacted_excerpts_changes(configured_environment, monkeypatch):
    from novel_generator.dependencies import get_session_factory
    calls = install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        context = editor._book_context(run, run.chapters, ManuscriptQaReport())
        editor._review(session, run, run.chapters, context, object(), 'whole_book')
        editor._review(session, run, run.chapters, context, object(), 'whole_book')
        assert len(calls) == 1
        run.chapters[0].content += ' A different fact now occurs outside the saved compacted excerpts.'
        session.commit()
        editor._review(session, run, run.chapters, context, object(), 'whole_book')
        assert len(calls) == 2


def test_unsupported_chapter_reordering_is_rejected_before_editing():
    data = clean_review([16, 17])
    data['issues'] = [dict(issue(number=17), repair_instruction='Reorder the chapters so Chapter 17 precedes Chapter 16.')]
    from novel_generator.services.autonomous_contracts import EditorialReview
    with pytest.raises(ValueError, match='chapter order'):
        editor.validate_chapter_repair_scope(EditorialReview.model_validate(data))
    data['issues'][0]['repair_instruction'] = 'Do not reorder the chapters. Correct the premature claim in this chapter.'
    editor.validate_chapter_repair_scope(EditorialReview.model_validate(data))
    data['issues'][0]['repair_instruction'] = "Reorder the chapter's sections so the cause precedes its consequences."
    editor.validate_chapter_repair_scope(EditorialReview.model_validate(data))
    data['issues'][0]['repair_instruction'] = 'Move chapter 17 before chapter 16.'
    with pytest.raises(ValueError, match='chapter order'):
        editor.validate_chapter_repair_scope(EditorialReview.model_validate(data))


def test_unchanged_chapter_proposal_retries_from_source_and_uses_one_repair_attempt(configured_environment, monkeypatch):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.services import pipeline
    from test_autonomous_editorial import PROSE, REPAIRED
    calls = install_fake_provider(monkeypatch, revision=PROSE)
    original_chat = pipeline._supervised_provider_chat
    proposals = []
    def chat(*args, **kwargs):
        output = original_chat(*args, **kwargs)
        if kwargs['stage'] == 'autonomous_revision':
            proposals.append(args[5])
            if len(proposals) > 1: return REPAIRED
        return output
    monkeypatch.setattr(pipeline, '_supervised_provider_chat', chat)
    with get_session_factory()() as session:
        run = make_run(session)
        diagnosis = editor.validate_review(json.dumps({**clean_review([1]),
            'canon_consistent': False, 'issues': [dict(issue(), repair_scope='chapter')]}), run.chapters)
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        editor._repair(session, run, run.chapters[0], ledger, diagnosis.issues, get_settings(), object(), 'final')
        assert run.chapters[0].content == REPAIRED
        assert calls.count(('autonomous_revision', 1)) == 2
        assert len(editor._events(run, 'autonomous_repair_started')) == 1
        assert len(editor._events(run, 'autonomous_repair_candidate_rejected')) == 1
        assert proposals[1][:len(proposals[0])] == proposals[0]
        assert not any(message['role'] == 'assistant' for message in proposals[1])
        assert not editor._events(run, 'autonomous_quality_passed')


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
