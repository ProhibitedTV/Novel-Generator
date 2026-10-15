import pytest
from novel_generator.dependencies import get_session_factory
from novel_generator.settings import get_settings
from novel_generator.services import autonomous_editorial as editor
from test_autonomous_editorial import make_run,install_fake_provider,clean_review

BAD="Mara's father, Ivo, waited by the ferry with the ledger."
GOOD="Mara's brother, Ivo, waited by the ferry with the ledger."

@pytest.mark.parametrize('confirmed',[False,True])
def test_identity_gate_runs_after_general_pass_and_verifies_before_repair(configured_environment,monkeypatch,confirmed):
    scopes=[]
    def review(context,numbers):
        scopes.append(context['scope'])
        value=clean_review(numbers)
        if context['scope']=='canon_identity' and 'father' in context['actual_prose']:
            assert context['stable_character_canon']['relationships'][0]['relationship_to_protagonist']=='brother'
            if 'proposed_review_to_verify' not in context or confirmed:
                value.update(canon_consistent=False,issues=[{'chapter_number':1,'category':'character',
                    'problem':'The brother is described as the father.', 'evidence':'Source paragraphs','evidence_paragraphs':[1],
                    'repair_instruction':'Keep the established sibling relationship, changing only the incorrect role.', 'repair_scope':'passage'}])
        return value
    calls=install_fake_provider(monkeypatch,review_fn=review,revision=GOOD)
    with get_session_factory()() as session:
        run=make_run(session)
        run.story_bible={**run.story_bible,'character_agendas':[{'name':'Ivo','relationship_to_protagonist':'brother'}]}
        source=BAD if confirmed else 'Mara watched her brother Ivo play a father onstage by the ferry.'
        run.chapters[0].content=source;session.commit()
        editor.ensure_chapter(session,run,run.chapters[0],None,get_settings(),object())
        assert run.chapters[0].content==(GOOD if confirmed else source)
        assert bool(editor._events(run,'autonomous_repair_started')) is confirmed
        assert scopes[:3]==['chapter','canon_identity','canon_identity']
        assert scopes[3:]==(['chapter','canon_identity'] if confirmed else [])
        # Both audits are reusable after restart for exactly the same source/canon.
        before=len(calls);session.expire_all()
        editor.ensure_chapter(session,run,run.chapters[0],None,get_settings(),object())
        assert len(calls)==before


def test_changed_canon_invalidates_draft_identity_evidence(configured_environment,monkeypatch):
    calls=install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run=make_run(session)
        run.story_bible={**run.story_bible,'character_agendas':[{'name':'Ivo','relationship_to_protagonist':'brother'}]}
        session.commit()
        editor.ensure_chapter(session,run,run.chapters[0],None,get_settings(),object())
        before=len(calls)
        run.story_bible={**run.story_bible,'character_agendas':[{'name':'Ivo','relationship_to_protagonist':'cousin'}]}
        session.commit()
        editor.ensure_chapter(session,run,run.chapters[0],None,get_settings(),object())
        assert len(calls)==before+2
        assert editor._events(run,'autonomous_review_completed')[-1]['scope']=='canon_identity'
