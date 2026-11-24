import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.schemas import DevelopmentalRewritePlan, ManuscriptQaReport
from novel_generator.services.editorial_agenda import (
    EVENT_TYPE, agenda_event, agenda_summary, load_agenda, save_agenda, source_manifest,
)
from novel_generator.repositories import record_event, get_run
from test_autonomous_editorial import make_run


def test_saved_agenda_survives_restart_and_preserves_original_assessment(configured_environment):
    factory = get_session_factory()
    with factory() as session:
        run = make_run(session, chapters=2)
        run_id = run.id
        initial = source_manifest(run.chapters)
        plan = DevelopmentalRewritePlan(overall_diagnosis="Preserve causal consequences",
            chapter_actions=[{"chapter_numbers": [1], "action": "rewrite", "reason": "Motivation is unclear"},
                             {"chapter_numbers": [2], "action": "keep"}])
        qa = ManuscriptQaReport(overall_verdict="Original assessment")
        saved = save_agenda(session, run, run.chapters, qa, plan, "memo", "outline", "comparison")
        assert saved.source_hashes == initial
        assert agenda_summary(run)["rows"][0]["status"] == "Pending"
        run.chapters[0].content = "A revised causal scene."
        record_event(session, run, "developmental_chapter_revision_completed", {"chapter_number": 1})
        session.commit()
    with factory() as session:
        run = get_run(session, run_id)
        agenda = load_agenda(session, run, run.chapters)
        assert agenda.agenda_id == saved.agenda_id
        assert agenda.qa_report.overall_verdict == "Original assessment"
        assert agenda.plan == plan
        assert agenda.rewrite_markdown == "memo"
        assert agenda.source_hashes != source_manifest(run.chapters)
        again = save_agenda(session, run, run.chapters, ManuscriptQaReport(),
                            DevelopmentalRewritePlan(), "new", "new", "new")
        assert again.agenda_id == saved.agenda_id
        assert sum(e.event_type == EVENT_TYPE for e in run.events) == 1
        rows = agenda_summary(run)["rows"]
        assert rows[0]["status"] == "Revision saved"
        assert rows[1]["status"] == "No revision assigned"


def test_invalid_agenda_fails_without_mutating_prose(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        save_agenda(session, run, run.chapters, ManuscriptQaReport(),
                    DevelopmentalRewritePlan(), "memo", "outline", "comparison")
        before = run.chapters[0].content
        event = agenda_event(session, run.id)
        event.payload = {**event.payload, "rewrite_markdown": "changed"}
        session.commit()
        with pytest.raises(RuntimeError, match="checksum"):
            load_agenda(session, run, run.chapters)
        assert run.chapters[0].content == before
        assert agenda_summary(run)["invalid"]
