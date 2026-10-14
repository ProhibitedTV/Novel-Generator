from copy import deepcopy
from types import SimpleNamespace

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import get_run, replace_artifacts
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services import autonomous_editorial as editor
from novel_generator.services.editorial_coverage import review_coverage
from novel_generator.settings import get_settings
from test_autonomous_editorial import make_run, install_fake_provider, clean_review, issue, PROSE, REPAIRED


def test_acceptance_requires_all_distant_pairs_and_separate_developmental_reviews(configured_environment, monkeypatch):
    calls = install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run = make_run(session, chapters=4)
        editor.finish_manuscript(session, run, run.chapters, get_settings(), object(), ManuscriptQaReport())
        coverage = review_coverage(run)
        assert coverage["accepted"] and coverage["acceptance_contract"] == 2
        assert coverage["developmental_passes"] == 4 and coverage["cross_passes"] == 3
        assert coverage["identity_passes"] == 4
        pair_reviews = [e for e in editor._events(run, "autonomous_review_completed") if e["scope"] == "cross_chapter"]
        assert [p["review"]["reviewed_chapters"] for p in pair_reviews] == [[1, 3], [1, 4], [2, 4]]
        # Removing a required audit cannot be hidden behind an aggregate pass marker.
        lost = next(e for e in run.events if e.event_type == "autonomous_review_completed" and e.payload.get("scope") == "cross_chapter")
        session.delete(lost)
        session.commit()
        session.expire(run, ["events"])
        assert not review_coverage(run)["accepted"]


def test_cross_pair_source_references_and_intervening_source_changes_invalidate_cache(configured_environment, monkeypatch):
    calls = install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run = make_run(session, chapters=3)
        ledger = editor._pipeline()._build_initial_ledger(editor._pipeline()._story_bible_from_run(run))
        pair = [run.chapters[0], run.chapters[2]]
        def review():
            return editor._review(session, run, pair, editor._cross_chapter_context(run, pair, ledger), object(), "cross_chapter")
        assert review().passed
        assert review().passed
        assert len(calls) == 1
        run.chapters[1].content = REPAIRED
        session.commit()
        assert review_coverage(run)["cross_passes"] == 0
        assert review().passed
        assert len(calls) == 2
        assert review_coverage(run)["cross_passes"] == 1
        marker = editor._coverage_marker([1, 3], "cross_chapter", paragraph_counts={1: 1, 3: 1})
        from novel_generator.services.structured_schema_runtime import extract_schema_marker
        _, schema, _ = extract_schema_marker([marker])
        assert schema["properties"]["reviewed_chapters"]["const"] == [1, 3]
        assert "source_references" in schema["$defs"]["EditorialIssue"]["required"]


def test_later_source_bound_editorial_concerns_override_prior_acceptance(configured_environment, monkeypatch):
    install_fake_provider(monkeypatch)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        editor.finish_manuscript(session, run, run.chapters, get_settings(), object(), ManuscriptQaReport())
        before = [c.content for c in run.chapters]
        assert review_coverage(run)["accepted"]
        editor._record(session, run, "editorial_concerns_recorded", {
            "manuscript_hash": review_coverage(run)["manuscript_hash"], "message": "Substantial revision required.",
            "issues": [{"chapter_number": 2, "problem": "A later review identified a conflicting transfer."}]})
        coverage = review_coverage(run)
        assert not coverage["accepted"] and coverage["editorial_concerns"]
        assert [c.content for c in run.chapters] == before


def test_confirmed_distant_contradiction_blocks_exports_and_cannot_be_ignored(configured_environment, monkeypatch):
    def review(context, numbers):
        output = clean_review(numbers)
        if context["scope"] == "cross_chapter":
            output["canon_consistent"] = False
            output["issues"] = [{**issue(number=3), "evidence": "Source paragraphs", "evidence_paragraphs": [],
                "source_references": [{"chapter_number": 1, "paragraph_number": 1}, {"chapter_number": 3, "paragraph_number": 1}]}]
        return output
    install_fake_provider(monkeypatch, review_fn=review, revision=PROSE)
    with get_session_factory()() as session:
        run = make_run(session, chapters=3)
        with pytest.raises(editor.AutonomousQualityError):
            editor.finish_manuscript(session, run, run.chapters, get_settings(), object(), ManuscriptQaReport())
        assert not editor._events(run, "autonomous_quality_passed")
        assert not {"markdown", "docx"} & {a.kind for a in run.artifacts}


def test_overlapping_repairs_restart_audit_instead_of_applying_stale_evidence(configured_environment, monkeypatch):
    repaired = []
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        review = clean_review([1, 2])
        review["issues"] = [issue(number=1), issue(number=2)]
        validated = editor.validate_review(__import__("json").dumps(review), run.chapters)
        def repair(session, run, chapter, *args):
            repaired.append(chapter.chapter_number)
            chapter.content = REPAIRED
        monkeypatch.setattr(editor, "_repair", repair)
        editor._apply_final_repairs(session, run, run.chapters, {1: None, 2: None}, validated.issues, get_settings(), object(), 0)
        assert repaired == [1]
        assert run.chapters[1].content == PROSE
        assert editor._events(run, "autonomous_reaudit_required")


def test_nonfiction_collection_and_deliberate_motifs_retain_book_form_contract(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session, chapters=3)
        run.story_bible = {**run.story_bible, "book_spec": {"content_kind": "nonfiction", "structure": "collection"}}
        context = editor._developmental_context(run, run.chapters[1], None)
        assert context["author_brief"]["structure"] == "collection"
        assert "independent collection" in editor.DEVELOPMENTAL_RULES
        assert "Hypothetical examples" in editor._cross_chapter_context(run, [run.chapters[0],run.chapters[2]], None).get("source_scope", "") or "hypothetical examples" in editor.BOOKWIDE_CONTINUITY_RULES


def test_reconstruction_is_idempotent_and_preserves_source_and_plan_after_export(client, monkeypatch):
    from novel_generator.services.providers import ProviderManager
    monkeypatch.setattr(ProviderManager, "ensure_model", lambda *a: None)
    with get_session_factory()() as session:
        source = make_run(session, chapters=2)
        source.status = RunStatus.COMPLETED
        session.commit()
        source_id = source.id
        old_brief = deepcopy(source.project.story_brief)
        old_prose = [c.content for c in source.chapters]
    request = {"revision_plan": "Rebuild the timeline and keep the transfer irreversible.", "brief_updates": {"style_targets": ["Distinct character voices"]}}
    response = client.post(f"/api/runs/{source_id}/reconstruct", json=request)
    assert response.status_code == 200
    run_id = response.json()["id"]
    assert client.post(f"/api/runs/{source_id}/reconstruct", json=request).json()["id"] == run_id
    with get_session_factory()() as session:
        source, run = get_run(session, source_id), get_run(session, run_id)
        assert source.project.story_brief == old_brief and [c.content for c in source.chapters] == old_prose
        assert run.project_id != source.project_id and run.source_run_id == source.id
        assert not run.chapters and run.outline is None and run.story_bible is None
        assert run.quality_profile == "autonomous" and not run.pause_after_outline
        replace_artifacts(session, run, [])
        assert any(a.kind == "reconstruction-plan" for a in run.artifacts)
    assert "original manuscript is preserved" in client.get(f"/runs/{run_id}").text.lower()


def test_reconstruction_rejects_unknown_brief_fields_and_incomplete_source(client, monkeypatch):
    from novel_generator.services.providers import ProviderManager
    monkeypatch.setattr(ProviderManager, "ensure_model", lambda *a: None)
    with get_session_factory()() as session:
        source = make_run(session)
        source_id = source.id
    request = {"revision_plan": "Reconstruct the manuscript without duplicate endings."}
    assert client.post(f"/api/runs/{source_id}/reconstruct", json=request).status_code == 409
    assert client.post("/api/runs/missing/reconstruct", json=request).status_code == 404
    with get_session_factory()() as session:
        source = get_run(session, source_id)
        source.status = RunStatus.COMPLETED
        session.commit()
    assert client.post(f"/api/runs/{source_id}/reconstruct", json={**request, "brief_updates": {"misspelled_field": "bad"}}).status_code == 409


def test_recorded_source_concerns_block_publication_helper_but_preserve_downloadable_prose(client):
    with get_session_factory()() as session:
        source = make_run(session)
        source.status = RunStatus.COMPLETED
        editor._record(session, source, "editorial_concerns_recorded", {
            "manuscript_hash": review_coverage(source)["manuscript_hash"], "message": "Substantial revision required.",
            "issues": [{"chapter_number": 1, "problem": "Conflicting family roles."}]})
        run_id = source.id
    response = client.post(f"/runs/{run_id}/publication-export", data={"profile_id": "ebook_docx"}, follow_redirects=False)
    assert response.status_code == 303 and "recorded+editorial+concerns" in response.headers["location"]
    page = client.get(f"/runs/{run_id}")
    assert "Completed manuscript needs revision" in page.text and "Revision required" in page.text
    with get_session_factory()() as session:
        source = get_run(session, run_id)
        assert source.chapters[0].content == PROSE
        assert not any(a.kind.startswith("publication-") for a in source.artifacts)
