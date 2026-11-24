from datetime import datetime, timedelta

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import claim_next_queued_run, begin_stage_attempt, complete_stage_attempt, get_run
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


def test_successful_inference_resets_persisted_outage_budget_and_backoff(configured_environment):
    settings = get_settings().model_copy(update={"provider_recovery_attempts": 1})
    factory = get_session_factory()
    with factory() as session:
        run = make_run(session)
        run_id = run.id
    for outage in range(3):
        with factory() as session:
            run = get_run(session, run_id)
            assert schedule_provider_retry(session, run, settings, ProviderTransportError("offline"))
            event = run.events[-1]
            assert event.event_type == 'provider_retry_scheduled'
            assert event.payload['attempt'] == 1
            assert bool(event.payload['last_successful_inference_at']) is (outage > 0)
            due = datetime.fromisoformat(event.payload['retry_at'])
            assert 29 <= (due - event.created_at).total_seconds() <= 31
            assert not schedule_provider_retry(session, run, settings, ProviderTransportError("still offline"))
            assert release_provider_retries(session, now=due) == 1
            assert claim_next_queued_run(session).id == run_id
            attempt = begin_stage_attempt(session, run, stage='chapter_summary', chapter_number=1,
                provider_name='ollama', model_name='test-model')
            complete_stage_attempt(session, attempt, 'Useful model output')
            session.commit()
    with factory() as session:
        run = get_run(session, run_id)
        assert len([e for e in run.events if e.event_type == 'provider_retry_scheduled']) == 3
        assert run.chapters[0].content == PROSE


@pytest.mark.parametrize('attempt_status',['running','failed','interrupted'])
def test_unfinished_or_unsuccessful_inference_does_not_reset_outage_budget(configured_environment,attempt_status):
    with get_session_factory()() as session:
        run = make_run(session)
        settings = get_settings().model_copy(update={"provider_recovery_attempts": 1})
        assert schedule_provider_retry(session, run, settings, ProviderTransportError('offline'))
        attempt = begin_stage_attempt(session, run, stage='autonomous_review', chapter_number=1,
            provider_name='ollama', model_name='test-model')
        attempt.status = attempt_status
        if attempt_status != 'running':
            attempt.completed_at = datetime.utcnow()
        session.commit()
        assert not schedule_provider_retry(session, run, settings, ProviderTransportError('still offline'))


def test_another_books_success_cannot_reset_a_persistent_outage(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        settings = get_settings().model_copy(update={"provider_recovery_attempts": 1})
        assert schedule_provider_retry(session, run, settings, ProviderTransportError('offline'))
        other = make_run(session)
        attempt = begin_stage_attempt(session, other, stage='chapter_summary', chapter_number=1,
            provider_name='ollama', model_name='test-model')
        complete_stage_attempt(session, attempt, 'Another book made progress')
        session.commit()
        assert not schedule_provider_retry(session, run, settings, ProviderTransportError('still offline'))


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


def test_terminal_failure_records_original_stage_and_nested_cause(configured_environment, monkeypatch):
    def fail(*args):
        try:
            raise ValueError("Pacing requirement was not met")
        except ValueError as exc:
            raise RuntimeError("Outline rejected") from exc
    monkeypatch.setattr(pipeline, "process_run", fail)
    with get_session_factory()() as session:
        run = make_run(session)
        run.current_step = "outline"
        session.commit()
        pipeline.process_run_safe(session, run, get_settings(), object())
        session.refresh(run, ["events"])
        failure = next(e.payload for e in run.events if e.event_type == "run_failed")
        assert run.status == RunStatus.FAILED
        assert failure["failed_step"] == "outline"
        assert failure["error_type"] == "RuntimeError"
        assert failure["causes"][1]["message"] == "Pacing requirement was not met"
        assert run.chapters[0].content == PROSE


def test_live_status_distinguishes_provider_cooldown_and_redacts_common_credentials(configured_environment):
    from novel_generator.services.run_diagnostics import read_status, safe_message
    now = datetime(2026, 9, 22, 12)
    with get_session_factory()() as session:
        run = make_run(session)
        run.last_heartbeat_at = now
        session.flush()
        assert read_status(session, run.id, stale_after_seconds=60)["heartbeat_stale"]
        schedule_provider_retry(session, run, get_settings(), ProviderTransportError("offline"), now=now)
        status = read_status(session, run.id, stale_after_seconds=60)
        assert status["step"] == "provider_wait" and not status["heartbeat_stale"]
        assert status["retry_at_utc"] == (now + timedelta(seconds=30)).isoformat()
        assert "private-token" not in safe_message("Authorization: Bearer private-token")
        assert "my-password" not in safe_message("https://user:my-password@localhost")


def test_manual_resume_reopens_exhausted_provider_window_without_resetting_quality_history(configured_environment):
    from novel_generator.services.state import resume_failed_run
    from novel_generator.repositories import record_event
    settings = get_settings().model_copy(update={"provider_recovery_attempts": 1})
    with get_session_factory()() as session:
        run = make_run(session)
        record_event(session, run, "autonomous_repair_started", {"chapter_number": 1, "phase": "draft"})
        assert schedule_provider_retry(session, run, settings, ProviderTransportError("offline"))
        release_provider_retries(session, now=datetime.utcnow() + timedelta(hours=1))
        claim_next_queued_run(session)
        session.commit()
        assert not schedule_provider_retry(session, run, settings, ProviderTransportError("still offline"))
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        session.commit()
        resume_failed_run(session, run)
        session.commit()
        claim_next_queued_run(session)
        session.commit()
        assert schedule_provider_retry(session, run, settings, ProviderTransportError("offline again"))
        session.refresh(run, ["events"])
        schedules = [e.payload for e in run.events if e.event_type == "provider_retry_scheduled"]
        assert [e["attempt"] for e in schedules] == [1, 1]
        assert any(e.event_type == "provider_retry_exhausted" for e in run.events)
        assert len([e for e in run.events if e.event_type == "autonomous_repair_started"]) == 1
        assert not any(e.event_type == "autonomous_repair_interrupted" for e in run.events)
        assert run.chapters[0].content == PROSE


@pytest.mark.parametrize("broken", ["missing", "invalid", "missing_attempt"])
def test_invalid_provider_cooldown_does_not_strand_run_or_crash_worker(configured_environment, broken):
    from novel_generator.repositories import record_event
    with get_session_factory()() as session:
        run = make_run(session)
        run.status = RunStatus.QUEUED
        run.current_step = "provider_wait"
        if broken == "invalid":
            record_event(session, run, "provider_retry_scheduled", {"retry_at": "invalid", "attempt": 1})
        elif broken == "missing_attempt":
            record_event(session, run, "provider_retry_scheduled", {"retry_at": datetime.utcnow().isoformat()})
        session.commit()
        assert release_provider_retries(session) == 0
        session.commit()
        session.refresh(run, ["events"])
        assert run.status == RunStatus.FAILED
        assert "Provider retry schedule is invalid" in run.error_message
        assert any(e.event_type == "provider_retry_invalid" for e in run.events)
        assert run.chapters[0].content == PROSE


def test_worker_restart_does_not_charge_unfinished_editorial_inference(configured_environment):
    from novel_generator.repositories import record_event, recover_running_runs
    with get_session_factory()() as session:
        run = make_run(session)
        run.current_step = "autonomous_revision"
        run.current_chapter = 1
        started = record_event(session, run, "autonomous_repair_started", {"chapter_number": 1, "phase": "draft"})
        session.flush()
        pending = begin_stage_attempt(session, run, stage="autonomous_revision", chapter_number=1,
                                      provider_name="ollama", model_name="test-model")
        session.commit()
        assert recover_running_runs(session, reason="exclusive_worker_startup") == 1
        session.commit()
        session.refresh(run, ["events"])
        marker = [e.payload for e in run.events if e.event_type == "autonomous_repair_interrupted"]
        assert len(marker) == 1
        assert marker[0]["started_event_id"] == started.id
        assert pending.status == "interrupted"
        assert run.chapters[0].content == PROSE
        assert recover_running_runs(session, reason="exclusive_worker_startup") == 0


def test_completed_repair_is_not_refunded_on_unrelated_interruption(configured_environment):
    from novel_generator.repositories import record_event, recover_running_runs
    with get_session_factory()() as session:
        run = make_run(session)
        run.current_step = "autonomous_revision"
        run.current_chapter = 1
        record_event(session, run, "autonomous_repair_started", {"chapter_number": 1, "phase": "draft"})
        record_event(session, run, "autonomous_repair_completed", {"chapter_number": 1, "phase": "draft"})
        begin_stage_attempt(session, run, stage="autonomous_revision", chapter_number=1,
                            provider_name="ollama", model_name="test-model")
        session.commit()
        recover_running_runs(session, reason="exclusive_worker_startup")
        session.commit()
        session.refresh(run, ["events"])
        assert not any(e.event_type == "autonomous_repair_interrupted" for e in run.events)
