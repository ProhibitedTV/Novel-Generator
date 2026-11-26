import json

import pytest

from novel_generator.services import chapter_range_repair as ranges
from novel_generator.services.structured_schema_runtime import extract_schema_marker


SOURCE = 'First correct paragraph.\r\n\r\nErroneous premature scene.\r\n\r\nCorrect ending.'


def test_replace_and_delete_ranges_preserve_unselected_source_bytes():
    fixed = ranges.apply_edits(SOURCE, {'edits': [dict(start_paragraph=2, end_paragraph=2,
        replacement_text='The decision remains unresolved.')]}, maximum_words=50)
    assert fixed == SOURCE.replace('Erroneous premature scene.', 'The decision remains unresolved.')
    deleted = ranges.apply_edits(SOURCE, {'edits': [dict(start_paragraph=2, end_paragraph=2,
        replacement_text='')]}, maximum_words=50)
    assert deleted == SOURCE.replace('Erroneous premature scene.', '')
    combined = ranges.apply_edits(SOURCE, {'edits': [
        dict(start_paragraph=3, end_paragraph=3, replacement_text='They departed.'),
        dict(start_paragraph=1, end_paragraph=2, replacement_text='They failed to reach an agreement.')
    ]}, maximum_words=50)
    assert combined == 'They failed to reach an agreement.\r\n\r\nThey departed.'


@pytest.mark.parametrize('edits,match', [
    ([dict(start_paragraph=2, end_paragraph=4, replacement_text='x')], 'inside'),
    ([dict(start_paragraph=3, end_paragraph=2, replacement_text='x')], 'ordered'),
    ([dict(start_paragraph=1, end_paragraph=2, replacement_text='x'),
      dict(start_paragraph=2, end_paragraph=3, replacement_text='y')], 'overlap'),
    ([dict(start_paragraph=1, end_paragraph=3, replacement_text='')], 'entire chapter'),
    ([dict(start_paragraph=2, end_paragraph=2, replacement_text='Erroneous premature scene.')], 'No usable'),
    ([dict(start_paragraph='2', end_paragraph=2, replacement_text='x')], 'valid integer'),
    ([dict(start_paragraph=1, end_paragraph=3, replacement_text='word ' * 51)], 'ceiling'),
])
def test_invalid_edits_fail_atomically(edits, match):
    with pytest.raises(ValueError, match=match):
        ranges.apply_edits(SOURCE, {'edits': edits}, maximum_words=50)


def test_range_retry_keeps_original_source_and_exact_native_contract():
    calls = []
    def generate(messages, attempt):
        calls.append(messages)
        if attempt == 1:
            return json.dumps({'edits': [dict(start_paragraph=1, end_paragraph=99, replacement_text='x')]})
        return json.dumps({'edits': [dict(start_paragraph=2, end_paragraph=2, replacement_text='A corrected scene.')]})
    result = ranges.repair(SOURCE, [], {'actual_prose': SOURCE, 'chapter_word_range': [10, 50]}, generate)
    assert 'A corrected scene.' in result
    assert calls[1][:3] == calls[0]
    assert not any(message['role'] == 'assistant' for message in calls[1])
    clean, schema, _ = extract_schema_marker(calls[0])
    assert schema['$defs']['ChapterRangeEdit']['properties']['end_paragraph']['maximum'] == 3
    payload = json.loads(clean[-1]['content'])
    assert payload['source_paragraphs'][1]['text'] == 'Erroneous premature scene.'
    assert 'actual_prose' not in payload['read_only_context']


def test_full_rewrite_noops_switch_to_ranges_without_resetting_budget(configured_environment, monkeypatch):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.settings import get_settings
    from novel_generator.services import autonomous_editorial as editor, pipeline
    from test_autonomous_editorial import make_run, clean_review, issue
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs['metadata'])
        if kwargs['metadata']['repair_mode'] == 'paragraph_ranges':
            clean, schema, _ = extract_schema_marker(args[5])
            assert schema['$defs']['ChapterRangeEdit']
            return json.dumps({'edits': [dict(start_paragraph=2, end_paragraph=2,
                                            replacement_text='They stayed outside.') ]})
        return SOURCE
    monkeypatch.setattr(pipeline, '_supervised_provider_chat', chat)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = SOURCE
        chapter.summary = 'Stale summary.'
        session.commit()
        diagnosis = editor.validate_review(json.dumps({**clean_review([1]), 'canon_consistent': False,
            'issues': [dict(issue(evidence='Erroneous premature scene.'), repair_scope='chapter')]}), [chapter])
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        editor._repair(session, run, chapter, ledger, diagnosis.issues, get_settings(), object(), 'final')
        assert chapter.content == SOURCE.replace('Erroneous premature scene.', 'They stayed outside.')
        assert chapter.summary is None
        assert [call['repair_mode'] for call in calls] == ['chapter', 'chapter', 'paragraph_ranges']
        assert len(editor._events(run, 'autonomous_repair_started')) == 1
        assert len(editor._events(run, 'autonomous_repair_strategy_changed')) == 1
        assert not editor._events(run, 'autonomous_quality_passed')


def test_unusable_range_fallback_preserves_saved_prose(configured_environment, monkeypatch):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.settings import get_settings
    from novel_generator.services import autonomous_editorial as editor, pipeline
    from test_autonomous_editorial import make_run, clean_review, issue
    def chat(*args, **kwargs):
        return '{} ' if kwargs['metadata']['repair_mode'] == 'paragraph_ranges' else SOURCE
    monkeypatch.setattr(pipeline, '_supervised_provider_chat', chat)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = SOURCE
        session.commit()
        diagnosis = editor.validate_review(json.dumps({**clean_review([1]), 'canon_consistent': False,
            'issues': [dict(issue(evidence='Erroneous premature scene.'), repair_scope='chapter')]}), [chapter])
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        with pytest.raises(editor.AutonomousQualityError, match='no usable change'):
            editor._repair(session, run, chapter, ledger, diagnosis.issues, get_settings(), object(), 'final')
        assert chapter.content == SOURCE
        assert not editor._events(run, 'autonomous_repair_completed')


def test_persisted_unresolved_structural_rewrite_skips_copying_source_again(configured_environment, monkeypatch):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.settings import get_settings
    from novel_generator.services import autonomous_editorial as editor, pipeline
    from test_autonomous_editorial import make_run, clean_review, issue
    def chat(*args, **kwargs):
        assert kwargs['metadata']['repair_mode'] == 'paragraph_ranges'
        return json.dumps({'edits': [dict(start_paragraph=2, end_paragraph=2,
                                        replacement_text='A correctly deferred event.')]})
    monkeypatch.setattr(pipeline, '_supervised_provider_chat', chat)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = SOURCE
        session.commit()
        diagnosis = editor.validate_review(json.dumps({**clean_review([1]), 'canon_consistent': False,
            'issues': [dict(issue(evidence='Erroneous premature scene.'), repair_scope='chapter')]}), [chapter])
        editor._record(session, run, 'autonomous_repair_started', {
            'chapter_number': 1, 'phase': 'final', 'repair_kind': 'chapter', 'attempt': 1,
            'issues': [diagnosis.issues[0].model_dump()], 'before_hash': editor._hash(SOURCE)})
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        editor._repair(session, run, chapter, ledger, diagnosis.issues, get_settings(), object(), 'final')
        attempts = editor._events(run, 'autonomous_repair_started')
        assert len(attempts) == 2 and attempts[-1]['attempt'] == 2
        assert chapter.content == SOURCE.replace('Erroneous premature scene.', 'A correctly deferred event.')
        assert not editor._events(run, 'autonomous_quality_passed')


@pytest.mark.parametrize("unsafe", ["truncation", "cycle"])
def test_range_fallback_retries_unsafe_candidate_before_saving(configured_environment, monkeypatch, unsafe):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.settings import get_settings
    from novel_generator.services import autonomous_editorial as editor, pipeline
    from novel_generator.services.autonomous_contracts import EditorialIssue
    from test_autonomous_editorial import make_run, issue
    source = "The deed was burned.\n\n" + "She walked across the silent orchard. " * 100
    source = source.strip()
    earlier = source.replace("burned", "lost", 1)
    corrected = source.replace("burned", "sealed", 1)
    range_prompts = []
    def chat(*args, **kwargs):
        assert chapter.content == source
        if kwargs["metadata"]["repair_mode"] != "paragraph_ranges":
            return source
        range_prompts.append(args[5])
        if len(range_prompts) == 1:
            edit = (dict(start_paragraph=1, end_paragraph=2, replacement_text="The deed was sealed.")
                    if unsafe == "truncation" else
                    dict(start_paragraph=1, end_paragraph=1, replacement_text="The deed was lost."))
        else:
            edit = dict(start_paragraph=1, end_paragraph=1, replacement_text="The deed was sealed.")
        return json.dumps({"edits": [edit]})
    monkeypatch.setattr("novel_generator.services.local_prose_repair.repair_plan", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = source
        session.commit()
        diagnosis = EditorialIssue.model_validate(issue(evidence="The deed was burned."))
        if unsafe == "cycle":
            editor._record(session, run, "autonomous_repair_started", {
                "chapter_number": 1, "phase": "final", "repair_kind": "chapter",
                "before_hash": editor._hash(earlier), "issues": [diagnosis.model_dump()],
            })
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        editor._repair(session, run, chapter, ledger, [diagnosis], get_settings(), object(), "final")
        assert chapter.content == corrected
        assert len(range_prompts) == 2
        assert ("Localized repair changed chapter length" if unsafe == "truncation" else
                "earlier rejected version") in range_prompts[1][-1]["content"]
        assert len(editor._events(run, "autonomous_repair_completed")) == 1


@pytest.mark.parametrize("successful", [True, False])
def test_repeated_structural_range_noops_use_anchored_prose_without_accepting_it(
        configured_environment, monkeypatch, successful):
    from novel_generator.dependencies import get_session_factory
    from novel_generator.settings import get_settings
    from novel_generator.services import autonomous_editorial as editor, pipeline
    from test_autonomous_editorial import make_run, clean_review, issue

    source = "Untouched opening.\n\n" + SOURCE + "\n\nUntouched closing."
    calls = []

    def chat(*args, **kwargs):
        metadata = kwargs["metadata"]
        calls.append(metadata["repair_mode"])
        assert chapter.content == source
        if metadata["repair_mode"] == "paragraph_ranges":
            return json.dumps({"edits": [dict(start_paragraph=3, end_paragraph=3,
                                              replacement_text="Erroneous premature scene.")]})
        assert metadata["repair_mode"] == "anchored_prose"
        payload = json.loads(args[5][1]["content"])
        original = payload["editable_passage"]
        assert original == SOURCE
        assert "actual_prose" not in payload["read_only_context"]
        return original.replace("Erroneous premature scene.", "They stayed outside.") if successful else original

    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = source
        chapter.summary = "Old summary."
        session.commit()
        diagnosis = editor.validate_review(json.dumps({**clean_review([1]), "canon_consistent": False,
            "issues": [dict(issue(evidence="Erroneous premature scene."), repair_scope="chapter")]}), [chapter])
        editor._record(session, run, "autonomous_repair_started", {
            "chapter_number": 1, "phase": "draft", "repair_kind": "chapter", "attempt": 1,
            "issues": [diagnosis.issues[0].model_dump()], "before_hash": editor._hash(source)})
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        if successful:
            editor._repair(session, run, chapter, ledger, diagnosis.issues, get_settings(), object(), "draft")
            assert chapter.content == source.replace("Erroneous premature scene.", "They stayed outside.")
            assert chapter.summary is None
            assert len(editor._events(run, "autonomous_repair_completed")) == 1
        else:
            with pytest.raises(editor.AutonomousQualityError, match="Anchored prose fallback"):
                editor._repair(session, run, chapter, ledger, diagnosis.issues, get_settings(), object(), "draft")
            assert chapter.content == source
            assert chapter.summary == "Old summary."
            assert not editor._events(run, "autonomous_repair_completed")
        assert calls == ["paragraph_ranges"] * 3 + ["anchored_prose"] * (1 if successful else 2)
        assert len(editor._events(run, "autonomous_repair_started")) == 2
        assert not editor._events(run, "autonomous_quality_passed")
        assert editor._events(run, "autonomous_repair_strategy_changed")[-1]["to_strategy"] == "anchored_prose"
