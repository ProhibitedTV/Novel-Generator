import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import Artifact, RunStatus
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.repositories import record_event
from novel_generator.settings import get_settings
from novel_generator.services import cover_prompts as covers, pipeline
from novel_generator.services.autonomous_editorial import _hash
from novel_generator.services.cover_contracts import CoverPromptPack
from test_autonomous_editorial import make_run


PROSE = "A brass key lay beside a red ledger. Beyond the window, an orchard faded into mist.\n\nShe closed the gate and shared the first apple."


def design():
    return dict(visual_motif_summary="The ledger, key, and orchard link accountability to the shared harvest.",
        image_continuity_tags=["brass key", "red ledger", "orchard"],
        motifs=[dict(id=key, description=quote, source_excerpt_id="c1.p1", evidence_quote=quote)
                for key, quote in [("key", "brass key"), ("ledger", "red ledger"), ("orchard", "orchard faded into mist")]],
        primary_concept_id="symbol",
        concepts=[dict(id=key, title=title, front_cover_prompt=prompt,
            negative_prompt="No text, lettering, watermarks, or clutter.",
            typography_safe_zones=dict(title="Leave the upper third empty for the title.", author="Leave the lower sixth empty for the author."),
            aspect_ratio="2:3", visual_motif_ids=[motif], genre_signal_notes="Quiet agrarian fiction with restrained warm color.",
            back_cover_background_prompt="An empty soft field of mist and ochre, with space for a blurb.")
            for key, title, motif, prompt in [
                ("symbol", "The Key", "key", "A single brass key on a dark ground, with a thin red thread curving below it."),
                ("scene", "The Orchard", "orchard", "A misty orchard seen through an open window, distant trees softened by morning light."),
                ("graphic", "The Ledger", "ledger", "A closed red ledger arranged diagonally against a pale unmarked ground, in a graphic composition.")]])


def completed_run(session):
    run = make_run(session, chapters=2)
    run.status = RunStatus.COMPLETED
    run.current_step = "completed"
    for chapter in run.chapters:
        chapter.content = PROSE
        chapter.word_count = len(PROSE.split())
    for kind in ("markdown", "docx"):
        session.add(Artifact(run_id=run.id, kind=kind, filename=f"book.{kind}", relative_path=f"{run.id}/book.{kind}", content_type="application/octet-stream"))
    record_event(session, run, "autonomous_quality_passed", {"manuscript_hash": _hash([(ch.chapter_number, ch.content) for ch in run.chapters])})
    session.commit(); session.refresh(run, attribute_names=["artifacts", "events"])
    return run


def test_design_requires_grounded_motifs_distinct_concepts_and_layout():
    excerpts = covers.source_excerpts([SimpleNamespace(chapter_number=1, title="First", content=PROSE)])
    assert len(covers.parse_design(json.dumps(design()), excerpts).concepts) == 3
    wrong = deepcopy(design()); wrong["motifs"][0]["evidence_quote"] = "a nonexistent blue dragon"
    with pytest.raises(ValueError, match="exact quotation"):
        covers.parse_design(json.dumps(wrong), excerpts)
    wrong = deepcopy(design()); wrong["concepts"][1]["front_cover_prompt"] = wrong["concepts"][0]["front_cover_prompt"]
    with pytest.raises(ValueError, match="distinct titles and front-cover prompts"):
        covers.parse_design(json.dumps(wrong), excerpts)
    wrong = deepcopy(design()); wrong["concepts"][0]["visual_motif_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="existing visual motifs"):
        covers.parse_design(json.dumps(wrong), excerpts)
    wrong = deepcopy(design()); wrong["concepts"] = wrong["concepts"][:2]
    with pytest.raises(ValueError):
        covers.parse_design(json.dumps(wrong), excerpts)


def test_excerpt_sampler_covers_large_books_with_explicit_bounded_source():
    chapters = [SimpleNamespace(chapter_number=i, title=str(i), content="Word " * 1000) for i in range(1, 65)]
    excerpts = covers.source_excerpts(chapters)
    assert {row["chapter_number"] for row in excerpts} == set(range(1, 65))
    assert sum(len(row["text"]) for row in excerpts) <= 24000
    assert all(row["text"] in chapters[row["chapter_number"] - 1].content for row in excerpts)


def test_unaccepted_or_changed_text_cannot_generate_a_cover(configured_environment):
    with get_session_factory()() as session:
        run = completed_run(session)
        run.status = RunStatus.RUNNING; run.current_step = "chapter_edit"
        with pytest.raises(ValueError, match="final accepted"):
            covers.require_stable_text(run, run.chapters)
        run.status = RunStatus.COMPLETED
        run.chapters[1].content += " Changed after acceptance."
        with pytest.raises(ValueError, match="not passed its final automatic checks"):
            covers.require_stable_text(run, run.chapters)


def test_generated_artifact_is_renderer_ready_and_reused_for_exact_text(configured_environment, monkeypatch):
    calls = []
    def generate(session, run, client, provider, model, messages, parser, label, stage):
        calls.append(messages())
        assert stage == "cover_prompts" and "not the complete manuscript" in messages()[-1]["content"]
        return parser(json.dumps(design()))
    monkeypatch.setattr(pipeline, "_generate_structured_output", generate)
    with get_session_factory()() as session:
        run = completed_run(session); original = [ch.content for ch in run.chapters]
        settings = get_settings()
        artifact = covers.generate_cover_prompt_pack(session, run, run.chapters, settings, object())
        saved = CoverPromptPack.model_validate_json((settings.artifacts_dir / artifact.relative_path).read_text(encoding="utf-8"))
        assert saved.manuscript_hash == _hash([(ch.chapter_number, ch.content) for ch in run.chapters])
        assert len(saved.design.concepts) == 3
        assert all("title space" in concept.front_cover_prompt and "author space" in concept.front_cover_prompt for concept in saved.design.concepts)
        assert {a.kind for a in run.artifacts} >= {"markdown", "docx"}
        session.refresh(run, attribute_names=["artifacts"])
        assert covers.generate_cover_prompt_pack(session, run, run.chapters, settings, object()).id == artifact.id
        assert len(calls) == 1 and [ch.content for ch in run.chapters] == original


def test_optional_provider_failure_keeps_text_exports_and_completed_status(configured_environment, monkeypatch):
    monkeypatch.setattr(covers, "generate_cover_prompt_pack", lambda *args: (_ for _ in ()).throw(RuntimeError("Provider offline")))
    with get_session_factory()() as session:
        run = completed_run(session)
        before = [ch.content for ch in run.chapters]
        settings = get_settings().model_copy(update={"generate_cover_prompts": True})
        pipeline._export_completed_run(session, run, run.chapters, settings,
            ManuscriptQaReport(overall_verdict="Accepted."), client=object())
        assert run.status == RunStatus.COMPLETED and run.current_step == "completed"
        assert [ch.content for ch in run.chapters] == before
        assert {a.kind for a in run.artifacts} >= {"markdown", "docx", "autonomous-quality-report"}
        assert any(e.event_type == "cover_prompt_pack_failed" for e in run.events)
        assert run.events[-1].event_type == "run_completed"


def test_concurrent_manuscript_change_invalidates_cover_output(configured_environment, monkeypatch):
    def generate(session, run, client, provider, model, messages, parser, label, stage):
        with get_session_factory()() as other:
            from novel_generator.repositories import get_run
            changed = get_run(other, run.id)
            changed.chapters[1].content += " A later manuscript correction."
            other.commit()
        return parser(json.dumps(design()))
    monkeypatch.setattr(pipeline, "_generate_structured_output", generate)
    with get_session_factory()() as session:
        run = completed_run(session)
        with pytest.raises(ValueError, match="not passed its final automatic checks"):
            covers.generate_cover_prompt_pack(session, run, run.chapters, get_settings(), object())
        assert not any(artifact.kind == "cover-prompt-pack" for artifact in run.artifacts)


def test_optional_cover_work_respects_cancellation(configured_environment, monkeypatch):
    monkeypatch.setattr(covers, "generate_cover_prompt_pack", lambda *args: (_ for _ in ()).throw(pipeline.RunCanceled("Canceled.")))
    with get_session_factory()() as session:
        run = completed_run(session)
        with pytest.raises(pipeline.RunCanceled):
            covers.generate_optional_cover_prompts(session, run, run.chapters,
                get_settings().model_copy(update={"generate_cover_prompts": True}), object())
