from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.repositories import create_chapters_from_outline, create_project, create_run
from novel_generator.schemas import ChapterContinuityUpdate, ManuscriptQaReport, ProjectCreate, RunCreate
from novel_generator.services import autonomous_editorial as editor, pipeline
from novel_generator.services.autonomous_contracts import EditorialReview
from novel_generator.services.prompts import parse_story_bible
from novel_generator.services.structured_schema_runtime import response_schema_for_stage
from novel_generator.settings import get_settings
from test_pipeline import _story_bible_json, _outline_json


PROSE = "Mara kept the paper ledger. Ivo waited beside the old ferry."
REPAIRED = "Mara locked the paper ledger. Ivo waited beside the old ferry."


def clean_review(numbers):
    return {"reviewed_chapters": numbers, "plot_coherent": True, "canon_consistent": True,
            "prose_clean": True, "ending_complete": True, "issues": []}


def issue(number=1, evidence=PROSE):
    return {"chapter_number": number, "category": "continuity", "problem": "The ledger must be secured.",
            "evidence": evidence, "repair_instruction": "Lock the ledger away."}


def make_run(session, chapters=1):
    project = create_project(session, ProjectCreate(
        title="Test book", premise="Keep the ledger safe.", desired_word_count=11 * chapters,
        requested_chapters=chapters, min_words_per_chapter=8, max_words_per_chapter=16,
        preferred_model="test-model",
    ))
    run = create_run(session, project, RunCreate(project_id=project.id, quality_profile="autonomous"))
    run.story_bible = json.loads(_story_bible_json())
    run.outline = json.loads(_outline_json(chapters))["chapters"]
    create_chapters_from_outline(session, run)
    session.flush()
    session.refresh(run, attribute_names=["chapters"])
    for chapter in run.chapters:
        chapter.content = PROSE
        chapter.word_count = 11
        chapter.summary = "The ledger is safe."
        chapter.status = ChapterStatus.COMPLETED
    run.status = RunStatus.RUNNING
    session.commit()
    return run


def install_fake_provider(monkeypatch, *, review_fn=None, revision=REPAIRED):
    calls = []

    def generate(session, run, client, provider, model, build_messages, parser, label, stage, chapter_number=None):
        calls.append((stage, chapter_number))
        if stage == "continuity_update":
            output = ChapterContinuityUpdate(
                chapter_outcome="The ledger is safe.", current_patch_status="Safe", world_state="Safe",
                timeline_entry=f"Chapter {chapter_number}: ledger secured.", open_threads=[],
                open_promises_by_name={}, memory_damage={}, trust_fractures={}, civilian_pressure_points=[],
                emotional_open_loops={},
            ).model_dump()
        else:
            assert stage == "autonomous_review"
            context = json.loads(build_messages()[-1]["content"])
            numbers = ([context["chapter_number"]] if context["scope"] != "whole_book"
                       else [item["chapter_number"] for item in context["chapter_map"]])
            output = review_fn(context, numbers) if review_fn else clean_review(numbers)
        return parser(json.dumps(output))

    def chat(session, run, client, provider, model, messages, *, stage, chapter_number=None, **kwargs):
        calls.append((stage, chapter_number))
        return revision if stage == "autonomous_revision" else "The ledger is secured beside the ferry."

    monkeypatch.setattr(pipeline, "_generate_structured_output", generate)
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    return calls


def test_review_requires_real_prose_evidence_and_exact_coverage():
    chapters = [SimpleNamespace(chapter_number=1, content=PROSE)]
    assert editor.validate_review(json.dumps(clean_review([1])), chapters).passed
    for numbers in ([], [2], [1, 1]):
        with pytest.raises(ValueError):
            editor.validate_review(json.dumps(clean_review(numbers)), chapters)
    review = clean_review([1])
    review["issues"] = [issue(evidence="This never happened in the manuscript.")]
    with pytest.raises(ValueError, match="actual prose"):
        editor.validate_review(json.dumps(review), chapters)
    review["issues"] = [issue(evidence="   ")]
    with pytest.raises(ValueError):
        editor.validate_review(json.dumps(review), chapters)


def test_invalid_review_retry_retains_source_prose_and_editorial_contract(monkeypatch):
    messages = [{"role": "system", "content": "Editorial contract"},
                {"role": "user", "content": PROSE}]
    invalid = clean_review([1])
    invalid["issues"] = [issue(evidence="Invented quotation")]
    calls = []
    def chat(*args, **kwargs):
        calls.append(args[5])
        return json.dumps(invalid if len(calls) == 1 else clean_review([1]))
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    review = pipeline._generate_structured_output(
        None, None, object(), "ollama", "test", lambda: messages,
        lambda raw: editor.validate_review(raw, [SimpleNamespace(chapter_number=1, content=PROSE)]),
        "editorial review", "autonomous_review", 1,
    )
    assert review.passed
    assert calls[1][:2] == messages
    assert "actual prose" in calls[1][-1]["content"]


def test_missing_boolean_checks_and_unevidenced_failures_are_not_passes():
    with pytest.raises(ValueError):
        EditorialReview.model_validate({"reviewed_chapters": [1], "issues": []})
    review = clean_review([1])
    review["plot_coherent"] = False
    with pytest.raises(ValueError, match="evidenced issue"):
        EditorialReview.model_validate(review)
    review = clean_review([1])
    review["ending_complete"] = False
    review["issues"] = [dict(issue(), category="prose")]
    with pytest.raises(ValueError, match="ending or causal"):
        EditorialReview.model_validate(review)
    schema = response_schema_for_stage("autonomous_review")
    assert set(clean_review([1])) <= set(schema["required"])


def test_evidence_accepts_ordered_excerpts_but_not_fabricated_or_reordered_segments():
    source = "Mara kept the paper ledger. Ivo waited beside the old ferry."
    assert editor._grounded_evidence("Mara kept the paper […] Ivo waited beside the old ferry...", source)
    assert not editor._grounded_evidence("Mara burned the paper […] Ivo waited beside the old ferry", source)
    assert not editor._grounded_evidence("Ivo waited beside the old ferry […] Mara kept the paper", source)
    assert not editor._grounded_evidence("Mara ... the", source)


def test_autonomous_mode_skips_approval_and_requires_feasible_length(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        assert not run.pause_after_outline
        assert run.developmental_rewrite_enabled
        with pytest.raises(ValueError, match="reachable"):
            create_run(session, run.project, RunCreate(project_id=run.project_id, quality_profile="autonomous", target_word_count=1000))


def test_chapter_repairs_are_rereviewed_and_checkpointed(configured_environment, monkeypatch):
    def review(context, numbers):
        output = clean_review(numbers)
        if "kept" in context["actual_prose"]:
            output["issues"] = [issue()]
        return output

    calls = install_fake_provider(monkeypatch, review_fn=review)
    with get_session_factory()() as session:
        run = make_run(session)
        ledger = pipeline._build_initial_ledger(parse_story_bible(_story_bible_json()))
        editor.ensure_chapter(session, run, run.chapters[0], ledger, get_settings(), object())
        assert run.chapters[0].content == REPAIRED
        assert run.chapters[0].summary is None
        assert run.chapters[0].continuity_update is None
        assert calls.count(("autonomous_review", 1)) == 2
        assert len(editor._events(run, "autonomous_repair_started")) == 1
        assert list((get_settings().artifacts_dir / run.id / "editorial-history").glob("*.md"))
        # Resume on exactly the accepted prose reuses the audit without additional model calls.
        before = len(calls)
        session.expire_all()
        editor.ensure_chapter(session, run, run.chapters[0], ledger, get_settings(), object())
        assert len(calls) == before
        # Any prose change invalidates that audit.
        run.chapters[0].content = REPAIRED + " Now."
        editor.ensure_chapter(session, run, run.chapters[0], ledger, get_settings(), object())
        assert len(calls) == before + 1


def test_local_repair_preserves_neighbors_and_requires_fresh_review(configured_environment, monkeypatch):
    original = "Mara arrived.\n\nIvo nodded. Ivo nodded.\n\nThe ledger stayed safe."
    def review(context, numbers):
        output = clean_review(numbers)
        if "Ivo nodded. Ivo nodded." in context["actual_prose"]:
            output["issues"] = [dict(issue(evidence="Ivo nodded. Ivo nodded."), category="repetition")]
        return output
    calls = install_fake_provider(monkeypatch, review_fn=review, revision="Ivo nodded.")
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = original
        session.commit()
        ledger = pipeline._build_initial_ledger(parse_story_bible(_story_bible_json()))
        editor.ensure_chapter(session, run, chapter, ledger, get_settings(), object())
        assert chapter.content == "Mara arrived.\n\nIvo nodded.\n\nThe ledger stayed safe."
        assert chapter.summary is None and chapter.continuity_update is None
        assert calls.count(("autonomous_review", 1)) == 2
        assert len(editor._events(run, "autonomous_repair_started")) == 1


def test_repair_budget_survives_resume_and_rejects_no_progress(configured_environment, monkeypatch):
    def review(context, numbers):
        output = clean_review(numbers)
        output["issues"] = [issue(evidence=context["actual_prose"])]
        return output

    calls = install_fake_provider(monkeypatch, review_fn=review, revision=PROSE)
    with get_session_factory()() as session:
        run = make_run(session)
        settings = get_settings().model_copy(update={"autonomous_chapter_repair_attempts": 1})
        ledger = pipeline._build_initial_ledger(parse_story_bible(_story_bible_json()))
        with pytest.raises(editor.AutonomousQualityError, match="no usable change"):
            editor.ensure_chapter(session, run, run.chapters[0], ledger, settings, object())
        session.expire_all()
        with pytest.raises(editor.AutonomousQualityError, match="exhausted"):
            editor.ensure_chapter(session, run, run.chapters[0], ledger, settings, object())
        assert calls.count(("autonomous_revision", 1)) == 1


def test_final_gate_cannot_accept_short_book_even_if_reviewer_approves(configured_environment, monkeypatch):
    install_fake_provider(monkeypatch, revision=PROSE)
    with get_session_factory()() as session:
        run = make_run(session)
        run.target_word_count = 16  # Still feasible, but current 11-word manuscript misses the target.
        session.commit()
        with pytest.raises(editor.AutonomousQualityError):
            editor.finish_manuscript(session, run, run.chapters, get_settings(), object(), ManuscriptQaReport())
        assert not editor._events(run, "autonomous_quality_passed")
        report = json.loads((get_settings().artifacts_dir / run.id / "autonomous-quality-report.json").read_text())
        assert report["status"] == "not_passed"


def test_final_gate_repairs_and_rebuilds_metadata_before_acceptance(configured_environment, monkeypatch):
    def review(context, numbers):
        output = clean_review(numbers)
        if context["scope"] == "whole_book" and "kept" in context["final_chapter_actual_prose"]:
            output["issues"] = [issue()]
        return output

    calls = install_fake_provider(monkeypatch, review_fn=review)
    with get_session_factory()() as session:
        run = make_run(session)
        result = editor.finish_manuscript(session, run, run.chapters, get_settings(), object(), ManuscriptQaReport())
        assert run.chapters[0].content == REPAIRED
        assert calls.count(("continuity_update", 1)) == 2
        assert calls.count(("chapter_summary", 1)) == 2
        assert len(editor._events(run, "autonomous_quality_passed")) == 1
        assert result.publication_readiness_label == "automated checks passed"
        assert len(editor._events(run, "autonomous_review_completed")) == 4


def test_whole_book_context_does_not_duplicate_final_prose(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        context = editor._book_context(run, run.chapters, ManuscriptQaReport())
        assert json.dumps(context).count(PROSE) == 1
        assert context["chapter_map"][0]["actual_prose_supplied_in_final_chapter_field"]


def test_metadata_refresh_invalidates_affected_suffix(configured_environment, monkeypatch):
    calls = install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        editor._refresh_metadata(session, run, run.chapters, object())
        first_count = len(calls)
        editor._refresh_metadata(session, run, run.chapters, object())
        assert len(calls) == first_count
        run.chapters[0].content = REPAIRED
        session.commit()
        # A new preceding outcome must force re-extraction of downstream continuity too.
        original = pipeline._generate_structured_output
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[8] == "continuity_update" and args[9] == 1:
                result.world_state = "The locked ledger is now in the ferry cabin."
            return result
        monkeypatch.setattr(pipeline, "_generate_structured_output", changed)
        editor._refresh_metadata(session, run, run.chapters, object())
        assert calls.count(("continuity_update", 2)) == 2


def test_unknown_context_capacity_failure_does_not_silently_truncate():
    with pytest.raises(editor.AutonomousQualityError, match="context"):
        editor._check_context([{"content": "x" * 9000}], SimpleNamespace(num_ctx=2048), "ollama")


def test_length_repair_overrides_loose_range_with_concrete_budget(configured_environment, monkeypatch):
    captured = []
    def chat(*args, **kwargs):
        captured.extend(args[5])
        return REPAIRED
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        chapter.content = PROSE + " " + PROSE
        session.commit()
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        editor._repair(session, run, chapter, ledger, editor._length_issues(run, chapter), get_settings(), object(), "final")
        assert json.loads(captured[1]["content"])["context"]["chapter_word_range"] == [11, 11]
        assert "current text is 22 words" in captured[0]["content"]
        assert "Keeping every existing sentence is not required" in captured[0]["content"]


def test_final_checkpoint_resume_skips_prior_edits_and_rechecks_before_export(configured_environment, monkeypatch):
    install_fake_provider(monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError("Final checkpoint resume must not redraft or repeat earlier edits.")
    for name in ("_draft_chapter", "_run_manuscript_qa", "_run_developmental_rewrite", "_run_final_editing_pass"):
        monkeypatch.setattr(pipeline, name, forbidden)
    with get_session_factory()() as session:
        run = make_run(session)
        editor.checkpoint_final_stage(session, run, ManuscriptQaReport(), rewrite_markdown="Saved editorial plan")
        run.chapters[0].status = ChapterStatus.FAILED
        run.chapters[0].error_message = "Provider interruption during final review"
        session.commit()
        pipeline.process_run_safe(session, run, get_settings(), object())
        session.refresh(run)
        assert run.status == RunStatus.COMPLETED
        assert run.chapters[0].status == ChapterStatus.COMPLETED
        assert run.chapters[0].error_message is None
        assert editor._events(run, "autonomous_quality_passed")
        assert {"markdown", "docx"} <= {artifact.kind for artifact in run.artifacts}


@pytest.mark.parametrize("reject", [False, True])
def test_real_pipeline_withholds_manuscript_exports_until_gate_passes(configured_environment, monkeypatch, reject):
    def review(context, numbers):
        output = clean_review(numbers)
        if reject:
            output["issues"] = [issue(evidence=PROSE)]
        return output

    install_fake_provider(monkeypatch, review_fn=review, revision=PROSE)
    monkeypatch.setattr(pipeline, "_run_manuscript_qa", lambda *args: (ManuscriptQaReport(), "QA"))
    monkeypatch.setattr(pipeline, "_run_developmental_rewrite", lambda *args: (None, None, None, None))
    for name in ("_run_developmental_revision_waves", "_run_publication_humanization_pass",
                 "_run_publication_compression_pass", "_run_final_editing_pass"):
        monkeypatch.setattr(pipeline, name, lambda *args: None)
    with get_session_factory()() as session:
        run = make_run(session)
        run.chapters[0].continuity_update = ChapterContinuityUpdate(
            chapter_outcome="The ledger is safe.", current_patch_status="Safe", world_state="Safe",
            timeline_entry="The ledger was secured.",
        ).model_dump()
        session.commit()
        pipeline.process_run_safe(session, run, get_settings(), object())
        session.refresh(run)
        kinds = {artifact.kind for artifact in run.artifacts}
        assert "autonomous-quality-report" in kinds
        assert run.status == (RunStatus.FAILED if reject else RunStatus.COMPLETED)
        if reject:
            assert not {"markdown", "docx"} & kinds
        else:
            assert {"markdown", "docx", "qa-report"} <= kinds
