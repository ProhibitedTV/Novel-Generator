from types import SimpleNamespace

from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.services.local_verification import verification_report
from novel_generator.services.autonomous_editorial import _hash


def _run(tmp_path):
    artifacts = []
    for kind in ("markdown", "docx", "qa-report"):
        (tmp_path / kind).write_text("test artifact", encoding="utf-8")
        artifacts.append(SimpleNamespace(kind=kind, relative_path=kind))
    return SimpleNamespace(
        id="test-run", model_name="local-model", status=RunStatus.COMPLETED, error_message=None,
        requested_chapters=2, target_word_count=1000, artifacts=artifacts,
        events=[SimpleNamespace(event_type="summary_fallback", payload={"chapter_number": 1})],
        stage_attempts=[],
        chapters=[SimpleNamespace(chapter_number=number, status=ChapterStatus.COMPLETED,
                                  content="Some actual prose.", summary="Summary", continuity_update={"world_state": "Known"},
                                  qa_notes={}) for number in (1, 2)],
    )


def test_completion_report_retains_quality_and_length_limits(tmp_path):
    report = verification_report(_run(tmp_path), tmp_path)
    assert report["pipeline_complete"]
    assert report["actual_words"] == 6
    assert report["target_words"] == 1000
    assert report["fallback_events"][0]["type"] == "summary_fallback"
    assert "not proof of logical consistency" in report["quality_note"]


def test_completion_report_rejects_missing_checkpoint_or_chapter(tmp_path):
    run = _run(tmp_path)
    run.chapters[0].continuity_update = None
    assert not verification_report(run, tmp_path)["pipeline_complete"]
    run.chapters.pop(0)
    assert not verification_report(run, tmp_path)["pipeline_complete"]


def test_completion_report_requires_all_export_formats(tmp_path):
    run = _run(tmp_path)
    (tmp_path / "docx").unlink()
    assert not verification_report(run, tmp_path)["pipeline_complete"]
    run.artifacts = [artifact for artifact in run.artifacts if artifact.kind != "docx"]
    assert not verification_report(run, tmp_path)["pipeline_complete"]


def test_automatic_acceptance_must_match_current_manuscript(tmp_path):
    run = _run(tmp_path)
    run.events.append(SimpleNamespace(event_type="autonomous_quality_passed", payload={
        "manuscript_hash": _hash([(chapter.chapter_number, chapter.content) for chapter in run.chapters])
    }))
    assert verification_report(run, tmp_path)["autonomous_checks_passed"]
    run.chapters[0].content = "A different ending."
    assert not verification_report(run, tmp_path)["autonomous_checks_passed"]
