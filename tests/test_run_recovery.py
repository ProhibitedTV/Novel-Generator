from datetime import datetime, timedelta

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import claim_next_queued_run
from novel_generator.services import pipeline
from novel_generator.services.provider_errors import ProviderError, ProviderTransportError
from novel_generator.services.run_recovery import schedule_provider_retry, release_provider_retries
from novel_generator.services.state import request_run_cancellation
from novel_generator.settings import get_settings
from test_autonomous_editorial import make_run, PROSE


def test_provider_retry_survives_session_restart_and_respects_cooldown(configured_environment):
    now = datetime(2026, 9, 22, 12)
    factory = get_session_factory()
    with factory() as session:
        run = make_run(session)
        run_id = run.id
        assert schedule_provider_retry(session, run, get_settings(), ProviderTransportError("offline"), now=now)
        assert claim_next_queued_run(session) is None
    with factory() as session:
        assert release_provider_retries(session, now=now + timedelta(seconds=29)) == 0
        assert claim_next_queued_run(session) is None
        assert release_provider_retries(session, now=now + timedelta(seconds=30)) == 1
        claimed = claim_next_queued_run(session)
        assert claimed.id == run_id
        assert claimed.chapters[0].content == PROSE
        assert claimed.recovery_count == 1
        session.commit()


def test_cancel_during_cooldown_prevents_automatic_restart(configured_environment):
    now = datetime(2026, 9, 22)
    with get_session_factory()() as session:
        run = make_run(session)
        schedule_provider_retry(session, run, get_settings(), ProviderTransportError("offline"), now=now)
        request_run_cancellation(session, run)
        session.commit()
        assert release_provider_retries(session, now=now + timedelta(days=1)) == 0
        assert claim_next_queued_run(session) is None
        assert run.status == RunStatus.CANCELED


@pytest.mark.parametrize("error", [ProviderError("model missing"), ValueError("invalid plot")])
def test_nontransient_failure_is_not_retried(configured_environment, error):
    with get_session_factory()() as session:
        run = make_run(session)
        assert not schedule_provider_retry(session, run, get_settings(), error)


def test_wrapped_transport_error_is_recovered_and_budget_is_persisted(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        error = RuntimeError("Outline failed")
        error.__cause__ = ProviderTransportError("offline")
        settings = get_settings().model_copy(update={"provider_recovery_attempts": 1})
        assert schedule_provider_retry(session, run, settings, error)
        session.expire_all()
        assert not schedule_provider_retry(session, run, settings, error)


def test_pipeline_transport_failure_queues_recovery_without_failing_chapter(configured_environment, monkeypatch):
    def fail(*args):
        raise ProviderTransportError("Connection refused")
    monkeypatch.setattr(pipeline, "process_run", fail)
    with get_session_factory()() as session:
        run = make_run(session)
        run.current_chapter = 1
        session.commit()
        pipeline.process_run_safe(session, run, get_settings(), object())
        assert run.status == RunStatus.QUEUED
        assert run.current_step == "provider_wait"
        assert run.chapters[0].content == PROSE
        assert not any(event.event_type == "run_failed" for event in run.events)


def test_interrupted_revision_does_not_spend_editorial_budget(configured_environment, monkeypatch):
    from novel_generator.services import autonomous_editorial as editor
    from novel_generator.services.autonomous_contracts import EditorialIssue
    from novel_generator.services.prompts import parse_story_bible
    from test_pipeline import _story_bible_json
    from test_autonomous_editorial import issue, REPAIRED
    def offline(*args, **kwargs):
        raise ProviderTransportError("offline")
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", offline)
    with get_session_factory()() as session:
        run = make_run(session)
        settings = get_settings().model_copy(update={"autonomous_chapter_repair_attempts": 1})
        ledger = pipeline._build_initial_ledger(parse_story_bible(_story_bible_json()))
        issues = [EditorialIssue.model_validate(issue())]
        with pytest.raises(ProviderTransportError) as failure:
            editor._repair(session, run, run.chapters[0], ledger, issues, settings, object(), "draft")
        assert schedule_provider_retry(session, run, settings, failure.value)
        release_provider_retries(session, now=datetime.utcnow() + timedelta(hours=1))
        claim_next_queued_run(session)
        session.commit()
        monkeypatch.setattr(pipeline, "_supervised_provider_chat", lambda *a, **kw: REPAIRED)
        editor._repair(session, run, run.chapters[0], ledger, issues, settings, object(), "draft")
        assert run.chapters[0].content == REPAIRED
        assert len(editor._events(run, "autonomous_repair_interrupted")) == 1
