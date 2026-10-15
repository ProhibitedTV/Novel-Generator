import pytest
from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.settings import get_settings
from novel_generator.services import pipeline
from test_autonomous_editorial import make_run

@pytest.mark.parametrize('corruption',['none','once','persistent'])
def test_production_completion_requires_matching_exports_and_retries_once(configured_environment,monkeypatch,corruption):
    original=pipeline.export_run_artifacts
    calls=[]
    def export(*args,**kwargs):
        artifacts=original(*args,**kwargs)
        calls.append(1)
        if corruption=='persistent' or (corruption=='once' and len(calls)==1):
            artifact=next(a for a in artifacts if a.kind=='markdown')
            (args[0]/artifact.relative_path).write_text('Stale incomplete export',encoding='utf-8')
        return artifacts
    monkeypatch.setattr(pipeline,'export_run_artifacts',export)
    with get_session_factory()() as session:
        run=make_run(session,chapters=2)
        run.quality_profile='balanced'
        source=[c.content for c in run.chapters]
        session.commit()
        def finish(*args):
            pipeline._export_completed_run(session,run,run.chapters,get_settings(),ManuscriptQaReport())
        if corruption=='persistent':
            monkeypatch.setattr(pipeline,'process_run',finish)
            pipeline.process_run_safe(session,run,get_settings(),object())
            assert run.status==RunStatus.FAILED
            assert not any(e.event_type=='run_completed' for e in run.events)
            assert 'export integrity' in run.error_message
        else:
            finish()
            assert run.status==RunStatus.COMPLETED
            verified=[e.payload for e in run.events if e.event_type=='artifact_export_verified']
            assert len(verified)==1 and all(row['matches_current_source'] for row in verified[0]['export_integrity'].values())
        assert len(calls)==(1 if corruption=='none' else 2)
        assert [c.content for c in run.chapters]==source
        assert len([e for e in run.events if e.event_type=='artifact_export_integrity_failed'])==({'none':0,'once':1,'persistent':2}[corruption])


@pytest.mark.parametrize('corrupt',[False,True])
def test_cancel_during_export_neither_completes_nor_retries(configured_environment,monkeypatch,corrupt):
    from novel_generator.services import image_packs
    monkeypatch.setattr(image_packs,'extract_optional_image_packs',lambda *args: pytest.fail('Optional work must not start after export cancellation'))
    original=pipeline.export_run_artifacts
    calls=[]
    with get_session_factory()() as session:
        run=make_run(session)
        run.quality_profile='balanced'
        source=run.chapters[0].content
        def export(*args,**kwargs):
            artifacts=original(*args,**kwargs)
            calls.append(1)
            if corrupt:
                artifact=next(a for a in artifacts if a.kind=='markdown')
                (args[0]/artifact.relative_path).write_text('Incomplete',encoding='utf-8')
            run.cancel_requested=True
            session.commit()
            return artifacts
        monkeypatch.setattr(pipeline,'export_run_artifacts',export)
        monkeypatch.setattr(pipeline,'process_run',lambda *args: pipeline._export_completed_run(session,run,run.chapters,get_settings(),ManuscriptQaReport()))
        pipeline.process_run_safe(session,run,get_settings(),object())
        assert run.status==RunStatus.CANCELED and len(calls)==1
        assert run.chapters[0].content==source
        assert not any(e.event_type=='run_completed' for e in run.events)
