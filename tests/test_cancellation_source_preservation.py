import pytest
from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import get_run, record_event, begin_stage_attempt, complete_stage_attempt
from novel_generator.services import pipeline
from test_autonomous_editorial import make_run

@pytest.mark.parametrize('cancellation',['none','requested','already_canceled'])
def test_cancellation_guard_preserves_pending_prose_and_canon(configured_environment,cancellation):
    factory=get_session_factory()
    with factory() as session:
        run=make_run(session)
        run_id=run.id
        pending='Newly revised prose must remain the actual source being reviewed.'
        run.chapters[0].content=pending
        bible={**run.story_bible,'pending_author_fact':'Do not discard this change.'}
        outline=[{**row,'objective':'A newly assigned objective.'} for row in run.outline]
        run.story_bible=bible
        run.outline=outline
        # Simulate the UI writing cancellation in an independent session.
        if cancellation!='none':
            with factory() as other:
                external=get_run(other,run_id)
                if cancellation=='requested':external.cancel_requested=True
                else:external.status=RunStatus.CANCELED
                other.commit()
            with pytest.raises(pipeline.RunCanceled):
                pipeline._ensure_not_canceled(session,run)
            assert run.status==RunStatus.CANCELED
        else:
            pipeline._ensure_not_canceled(session,run)
            session.commit()
        assert run.chapters[0].content==pending
        assert run.story_bible==bible and run.outline==outline
        session.expire_all()
        assert run.chapters[0].content==pending
        assert run.story_bible==bible and run.outline==outline
        assert run.last_heartbeat_at is not None


def test_new_checkpoints_are_visible_without_refreshing_pending_source(configured_environment):
    with get_session_factory()() as session:
        run=make_run(session)
        prior=list(run.events)
        pending='New source text remains intact while checkpoints are recorded.'
        run.chapters[0].content=pending
        event=record_event(session,run,'synthetic_checkpoint',{'saved':True})
        assert run.events==[*prior,event]
        assert run.chapters[0].content==pending
        session.commit()
        session.expire_all()
        assert run.events[-1].id==event.id
        assert run.chapters[0].content==pending


def test_stage_attempts_stay_visible_without_parent_refresh(configured_environment):
    with get_session_factory()() as session:
        run=make_run(session)
        assert run.stage_attempts==[]
        attempt=begin_stage_attempt(session,run,stage='chapter_edit',chapter_number=1,
            provider_name='ollama',model_name='test-model')
        assert run.stage_attempts==[attempt]
        complete_stage_attempt(session,attempt,'Revised source')
        assert run.stage_attempts[0].status=='success'
        session.commit()
        session.expire_all()
        assert run.stage_attempts[0].id==attempt.id
        assert run.stage_attempts[0].status=='success'
