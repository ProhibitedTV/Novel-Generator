import json
import pytest
from novel_generator.dependencies import get_session_factory
from novel_generator.settings import get_settings
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services import autonomous_editorial as editor
from test_autonomous_editorial import make_run,install_fake_provider,clean_review,PROSE

SYNOPSIS='The chapter ended with Mara watching the ferry.'

@pytest.mark.parametrize('confirmed',[False,True])
def test_suspected_artifact_requires_independent_evidence_verdict(configured_environment,monkeypatch,confirmed):
    def review(context,numbers):
        value=clean_review(numbers)
        if context['scope']=='output_artifacts' and ('proposed_review_to_verify' not in context or confirmed):
            value.update(prose_clean=False,issues=[{'chapter_number':1,'category':'prose',
                'problem':'Unintended synopsis describes the chapter instead of narrating the scene.',
                'evidence':'Source paragraphs','evidence_paragraphs':[1],
                'repair_instruction':'Narrate the character watching the ferry directly, preserving the event.',
                'repair_scope':'passage'}])
        return value
    calls=install_fake_provider(monkeypatch,review_fn=review)
    with get_session_factory()() as session:
        run=make_run(session);run.chapters[0].content=SYNOPSIS;session.commit()
        verdict=editor._output_artifact_review(session,run,run.chapters[0],object())
        assert verdict.passed is not confirmed
        assert len(calls)==2
        assert run.chapters[0].content==SYNOPSIS
        assert editor._events(run,'autonomous_review_adjudicated')[-1]['scope']=='output_artifacts'
        # Unchanged source reuses the independently adjudicated verdict.
        assert editor._output_artifact_review(session,run,run.chapters[0],object()).passed is not confirmed
        assert len(calls)==2
        run.chapters[0].content=PROSE
        assert editor._output_artifact_review(session,run,run.chapters[0],object()) is None

@pytest.mark.parametrize('text',[
    'The chapter ends with Mara watching the ferry.',SYNOPSIS,
    'This chapter shows how to secure a paper ledger.',
    '\"The chapter ended badly,\" Mara said, closing her novel.',
])
def test_intentional_book_form_and_dialogue_are_not_automatically_rejected(configured_environment,monkeypatch,text):
    install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run=make_run(session);run.chapters[0].content=text;session.commit()
        assert editor._output_artifact_review(session,run,run.chapters[0],object()).passed
        assert run.chapters[0].content==text
        assert not editor._events(run,'autonomous_repair_started')

@pytest.mark.parametrize('phase',['draft','final'])
def test_gate_repairs_confirmed_artifact_then_rechecks_source(configured_environment,monkeypatch,phase):
    def review(context,numbers):
        value=clean_review(numbers)
        if context['scope']=='output_artifacts':
            value.update(prose_clean=False,issues=[{'chapter_number':1,'category':'prose',
                'problem':'Unintended production synopsis.', 'evidence':'Source paragraphs','evidence_paragraphs':[1],
                'repair_instruction':'Render the scene directly, keeping the action.', 'repair_scope':'passage'}])
        return value
    install_fake_provider(monkeypatch,review_fn=review)
    with get_session_factory()() as session:
        run=make_run(session);run.chapters[0].content=SYNOPSIS;session.commit()
        if phase == 'draft':
            editor.ensure_chapter(session,run,run.chapters[0],None,get_settings(),object())
        else:
            editor.finish_manuscript(session,run,run.chapters,get_settings(),object(),ManuscriptQaReport())
        assert SYNOPSIS not in run.chapters[0].content
        assert editor._events(run,'autonomous_repair_completed')

def test_artifact_schema_limits_review_to_grounded_prose_issues():
    marker=editor._coverage_marker([1],'output_artifacts',paragraph_count=2)
    payload=json.loads(marker['content'])
    # The private marker wraps the generated schema.
    schema=payload.get('schema',payload)
    assert schema['properties']['canon_consistent']['const'] is True
    assert schema['$defs']['EditorialIssue']['properties']['category']['const']=='prose'
