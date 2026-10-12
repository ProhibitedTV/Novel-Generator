import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from novel_generator.dependencies import get_session_factory
from novel_generator.repositories import create_run
from novel_generator.schemas import RunCreate, ChapterContinuityUpdate
from novel_generator.settings import get_settings
from novel_generator.models import RunStatus
from novel_generator.services.ollama import OllamaClient
from novel_generator.services import outline_progression as progression, outline_recovery, pipeline
from novel_generator.services.autonomous_editorial import AutonomousQualityError, _events
from novel_generator.services.prompts import parse_story_bible
from test_autonomous_editorial import make_run
from test_pipeline import _story_bible_json


def clean(numbers):
    return dict(reviewed_chapters=numbers, progression_coherent=True, issues=[])


def defect(numbers):
    return dict(reviewed_chapters=numbers, progression_coherent=False, issues=[dict(
        chapter_numbers=[numbers[0], numbers[-1]], problem="The same first planting occurs twice without a reversal.",
        repair_instruction="Make the later event a distinct consequence of the established planting.")])


def test_progression_requires_exact_coverage_and_valid_source_references():
    outline = [dict(chapter_number=i) for i in range(1, 4)]
    assert progression.parse_review(json.dumps(clean([1, 2, 3])), outline).passed
    for payload in [clean([1, 1, 3]), {**clean([1, 2, 3]), "progression_coherent": False},
                    {**defect([1, 2, 3]), "issues": [dict(defect([1, 2, 3])["issues"][0], chapter_numbers=[1, 4])]},
                    {**defect([1, 2, 3]), "issues": [dict(defect([1, 2, 3])["issues"][0], chapter_numbers=[3, 1])]},
                    clean([True, 2, 3])]:
        with pytest.raises(ValueError):
            progression.parse_review(json.dumps(payload), outline)


@pytest.mark.parametrize("confirmed", [False, True])
def test_plan_defects_are_verified_before_mutation(configured_environment, monkeypatch, confirmed):
    calls = []
    def generate(session, run, client, provider, model, messages, parser, *args):
        payload = json.loads(messages()[-1]["content"])
        calls.append(payload)
        return parser(json.dumps(defect([1, 2]) if len(calls) == 1 or confirmed else clean([1, 2])))
    monkeypatch.setattr(pipeline, "_generate_structured_output", generate)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        before = deepcopy(run.outline)
        result = progression.review_outline(session, run, run.outline, object())
        assert result.passed is not confirmed
        assert run.outline == before
        assert len(calls) == 2
        assert "allegations_to_verify" in calls[1]
        assert "repair_instruction" not in calls[1]["allegations_to_verify"][0]
        assert _events(run, "outline_progression_adjudicated")


def test_approved_plan_cache_is_bound_to_source_and_author_contract(configured_environment, monkeypatch):
    calls = []
    def generate(session, run, client, provider, model, messages, parser, *args):
        calls.append(messages())
        return parser(json.dumps(clean([1, 2])))
    monkeypatch.setattr(pipeline, "_generate_structured_output", generate)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        for _ in range(2): progression.review_outline(session, run, run.outline, object())
        assert len(calls) == 1
        changed = deepcopy(run.outline);changed[1]["ending_state"] = "A different outcome."
        progression.review_outline(session, run, changed, object())
        assert len(calls) == 2
        run.project.premise = "A changed author premise."
        progression.review_outline(session, run, changed, object())
        assert len(calls) == 3


def test_rebuild_preserves_only_completed_prefix_and_parent_history(configured_environment):
    with get_session_factory()() as session:
        source = make_run(session, chapters=4)
        for chapter in source.chapters:
            chapter.continuity_update = {"chapter_outcome": "The ledger was secured."}
        session.commit()
        original = deepcopy(source.outline)
        original_text = [ch.content for ch in source.chapters]
        child = create_run(session, source.project, RunCreate(project_id=source.project_id,
            source_run_id=source.id, resume_from_chapter=3, rebuild_outline=True, quality_profile="autonomous"))
        session.commit();session.refresh(child, ["chapters", "events"])
        assert child.outline is None
        assert [ch.content for ch in child.chapters[:2]] == original_text[:2]
        assert all(ch.content is None and ch.plan is None for ch in child.chapters[2:])
        assert source.outline == original and [ch.content for ch in source.chapters] == original_text
        assert [e["chapter_number"] for e in progression.locked_prefix(session, child)] == [1, 2]
        assert _events(child, "outline_rebuild_requested")
        source.chapters[0].summary = None;child.chapters[0].summary = None
        with pytest.raises(AutonomousQualityError, match="complete prose and checkpoints"):
            progression.locked_prefix(session, child)


def test_rebuild_requires_a_source_and_resume_boundary():
    with pytest.raises(ValidationError, match="source run and resume chapter"):
        RunCreate(project_id="project", rebuild_outline=True)


def test_structural_replanning_preserves_prefix_and_attaches_actual_plan_evidence(configured_environment, monkeypatch):
    plans = []
    def spine(*args): return None
    def generate(session, run, bible, client, provider, model, **kwargs):
        plans.append(kwargs)
        return deepcopy(run._test_outline)
    reviews = iter([progression.parse_review(json.dumps(defect([1, 2, 3, 4])), [dict(chapter_number=i) for i in range(1, 5)]),
                    progression.parse_review(json.dumps(clean([1, 2, 3, 4])), [dict(chapter_number=i) for i in range(1, 5)])])
    monkeypatch.setattr(outline_recovery, "generate_outline", generate)
    monkeypatch.setattr(progression, "review_outline", lambda *args: next(reviews))
    monkeypatch.setattr(progression, "generate_spine", spine)
    monkeypatch.setattr(pipeline, "_validated_outline_or_fallback", lambda session, project, run, bible, outline, **kwargs: outline)
    with get_session_factory()() as session:
        run = make_run(session, chapters=4);run._test_outline = deepcopy(run.outline)
        result = progression.plan_outline(session, run, parse_story_bible(_story_bible_json()), object(), "ollama", "test", get_settings())
        assert result == run.outline and len(plans) == 2
        assert plans[1]["repairs"][0]["source_entries"][0]["ending_state"] == run.outline[0]["ending_state"]


def test_exhausted_plan_repairs_do_not_grant_acceptance(configured_environment, monkeypatch):
    monkeypatch.setattr(outline_recovery, "generate_outline", lambda session, run, *args, **kwargs: run.outline)
    monkeypatch.setattr(progression, "review_outline", lambda session, run, outline, *args:
        progression.parse_review(json.dumps(defect([1, 2])), outline))
    monkeypatch.setattr(pipeline, "_validated_outline_or_fallback", lambda session, project, run, bible, outline, **kwargs: outline)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        with pytest.raises(AutonomousQualityError, match="did not pass"):
            progression.plan_outline(session, run, parse_story_bible(_story_bible_json()), object(), "ollama", "test",
                get_settings().model_copy(update={"autonomous_outline_repair_attempts": 2}))
        assert len(_events(run, "outline_progression_repair_requested")) == 2


def test_pacing_repair_never_mutates_preserved_chapter_records(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session, chapters=4)
        outline = deepcopy(run.outline)
        bible = parse_story_bible(_story_bible_json())
        outline[0]["chapter_mode"] = "breather"
        outline[0]["outcome_type"] = "win"
        before = deepcopy(outline[0])
        repaired = pipeline._repair_outline_for_full_book_validation(
            run.project, bible, outline, 4, rebalance_modes=True, locked_chapters=1)
        assert repaired[0] == before


def test_spine_checkpoint_requires_the_entire_unwritten_suffix(configured_environment, monkeypatch):
    calls = []
    def generate(session, run, client, provider, model, messages, parser, *args):
        calls.append(json.loads(messages()[-1]["content"]))
        row = dict(chapter_number=2, title="Enactment", objective="Apply the treaty.",
                   ending_state="The gate is open.", new_progress="The water reaches the field.")
        with pytest.raises(ValueError, match="exactly chapters"):
            parser(json.dumps([dict(row, chapter_number=1)]))
        return parser(json.dumps([row]))
    monkeypatch.setattr(pipeline, "_generate_structured_output", generate)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        bible = parse_story_bible(_story_bible_json())
        prefix = deepcopy(run.outline[:1])
        first = progression.generate_spine(session, run, bible, object(), "ollama", "test", prefix, [])
        second = progression.generate_spine(session, run, bible, object(), "ollama", "test", prefix, [])
        assert first == second and first[0] == prefix[0] and len(calls) == 1
        progression.generate_spine(session, run, bible, object(), "ollama", "test", prefix, [{"problem": "A new defect."}])
        assert len(calls) == 2


@pytest.mark.parametrize("via_ui", [False, True])
def test_rebuild_request_reaches_the_repository_and_keeps_parent_history(client, monkeypatch, via_ui):
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["test-model"])
    with get_session_factory()() as session:
        source = make_run(session, chapters=2)
        source.status = RunStatus.FAILED
        source.chapters[0].continuity_update = ChapterContinuityUpdate(chapter_outcome="The ledger was secured.",
            current_patch_status="Safe", world_state="Safe", timeline_entry="Chapter 1: ledger secured.").model_dump()
        session.commit()
        source_id, project_id = source.id, source.project_id
        original = deepcopy(source.outline)
        text = source.chapters[0].content
    if via_ui:
        response = client.post(f"/runs/{source_id}/chapters/2/regenerate", data={"rebuild_outline": "true"}, follow_redirects=False)
        assert response.status_code == 303
        child_id = response.headers["location"].split("/runs/")[1].split("?")[0]
    else:
        response = client.post("/api/runs", json={"project_id": project_id, "source_run_id": source_id,
            "resume_from_chapter": 2, "rebuild_outline": True, "quality_profile": "autonomous"})
        assert response.status_code == 201
        child_id = response.json()["id"]
    with get_session_factory()() as session:
        from novel_generator.repositories import get_run
        child, parent = get_run(session, child_id), get_run(session, source_id)
        assert child.outline is None and _events(child, "outline_rebuild_requested")
        assert child.chapters[0].content == text and child.chapters[1].content is None
        assert parent.outline == original and parent.chapters[0].content == text
    if via_ui:
        detail = client.get(f"/runs/{source_id}")
        assert 'name="rebuild_outline"' in detail.text


def test_reused_bad_outline_is_blocked_before_drafting(configured_environment, monkeypatch):
    monkeypatch.setattr(progression, "review_outline", lambda session, run, outline, *args:
        progression.parse_review(json.dumps(defect([1, 2])), outline))
    monkeypatch.setattr(pipeline, "_draft_chapter", lambda *args: pytest.fail("A rejected outline must not reach drafting."))
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        with pytest.raises(AutonomousQualityError, match="Stored outline has confirmed progression defects"):
            pipeline.process_run(session, run, get_settings(), object())


def test_invalid_continuations_are_rejected_before_queueing(configured_environment):
    with get_session_factory()() as session:
        source = make_run(session, chapters=4)
        with pytest.raises(ValueError, match="Every preserved chapter"):
            create_run(session, source.project, RunCreate(project_id=source.project_id,
                source_run_id=source.id, resume_from_chapter=3, rebuild_outline=True))
        session.rollback()
        with pytest.raises(ValueError, match="chapter count requires rebuilding"):
            create_run(session, source.project, RunCreate(project_id=source.project_id,
                source_run_id=source.id, resume_from_chapter=2, requested_chapters=3))
