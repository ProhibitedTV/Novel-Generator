from datetime import datetime
import subprocess
import sys

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import get_run
from novel_generator.services.runner import recover_incomplete_runs
from novel_generator.services.worker_lease import exclusive_worker_lease
from novel_generator.settings import get_settings, Settings
from test_autonomous_editorial import make_run


def test_exclusive_startup_recovers_fresh_checkpoint_without_changing_prose(configured_environment):
    settings = get_settings().model_copy(update={"worker_exclusive": True})
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        run.status = RunStatus.RUNNING
        run.worker_id = "exited-worker:1"
        run.last_heartbeat_at = datetime.utcnow()
        before = [(c.content, c.summary, c.qa_notes, c.status) for c in run.chapters]
        run_id = run.id
        session.commit()
    with exclusive_worker_lease(settings) as owner:
        assert owner
        recover_incomplete_runs(settings, exclusive_owner=owner)
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        assert run.status == RunStatus.QUEUED and run.worker_id is None
        assert before == [(c.content, c.summary, c.qa_notes, c.status) for c in run.chapters]
        assert run.events[-1].payload["recovery_reason"] == "exclusive_worker_startup"


def test_default_startup_retains_fresh_other_owner_even_when_flag_is_set(configured_environment):
    settings = get_settings().model_copy(update={"worker_exclusive": True})
    with get_session_factory()() as session:
        run = make_run(session)
        run.status = RunStatus.RUNNING; run.worker_id = "live-other-worker:1"
        run.last_heartbeat_at = datetime.utcnow(); run_id = run.id; session.commit()
    # Configuration alone is insufficient: startup must actually acquire ownership.
    recover_incomplete_runs(settings)
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        assert run.status == RunStatus.RUNNING and run.worker_id == "live-other-worker:1"


def test_competing_worker_cannot_lock_then_can_after_owner_exit(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'book.db').as_posix()}", worker_exclusive=True)
    child_code = """
import sys
from novel_generator.settings import Settings
from novel_generator.services.worker_lease import exclusive_worker_lease
try:
    with exclusive_worker_lease(Settings(database_url=sys.argv[1],worker_exclusive=True)):
        print('owned',flush=True)
        sys.stdin.readline()
except RuntimeError:
    print('blocked',flush=True)
    sys.exit(23)
"""
    with exclusive_worker_lease(settings):
        child = subprocess.run([sys.executable,"-c",child_code,settings.database_url],input="\n",text=True,capture_output=True,timeout=15)
        assert child.returncode == 23 and "blocked" in child.stdout
    child = subprocess.Popen([sys.executable,"-c",child_code,settings.database_url],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(RuntimeError,match="Another exclusive worker"):
            with exclusive_worker_lease(settings):
                pytest.fail("Stole live owner")
        child.kill(); child.wait(timeout=10)
        # The OS releases ownership even without the child's cleanup/finally block.
        with exclusive_worker_lease(settings) as owned:
            assert owned
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=10)
        child.stdin.close(); child.stdout.close(); child.stderr.close()


def test_disabled_ownership_creates_no_file_and_remote_database_is_rejected(tmp_path):
    settings=Settings(database_url=f"sqlite:///{(tmp_path / 'book.db').as_posix()}")
    with exclusive_worker_lease(settings) as owned:
        assert not owned
    assert not list(tmp_path.iterdir())
    for url in ("sqlite:///:memory:","postgresql://localhost/books", "sqlite:///file:book.db?uri=true"):
        with pytest.raises(RuntimeError,match="local SQLite"):
            with exclusive_worker_lease(Settings(database_url=url,worker_exclusive=True)):
                pytest.fail("Unsupported ownership")


def test_fresh_interrupted_cancellation_is_honored_instead_of_stranded(configured_environment):
    settings = get_settings().model_copy(update={"worker_exclusive": True})
    with get_session_factory()() as session:
        run = make_run(session)
        run.status = RunStatus.RUNNING; run.last_heartbeat_at = datetime.utcnow()
        run.cancel_requested = True; run_id = run.id; before = run.chapters[0].content; session.commit()
    with exclusive_worker_lease(settings) as owner:
        recover_incomplete_runs(settings, exclusive_owner=owner)
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        assert run.status == RunStatus.CANCELED and run.current_step == "canceled"
        assert run.chapters[0].content == before and run.completed_at is not None
