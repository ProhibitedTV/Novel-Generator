from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from novel_generator.schemas import ChapterPlan, ChapterCritique, StoryBrief
from novel_generator.services.editorial import ChapterLintResult
from novel_generator.services import autonomous_editorial as editor, pipeline
from novel_generator.services.autonomous_contracts import EditorialReview
from novel_generator.services.book_specs import contract_text
from novel_generator.services.editorial import lint_chapter
from novel_generator.services.genre_profiles import genre_profile
from novel_generator.services.prompts import build_chapter_draft_messages, build_story_bible_messages, build_summary_messages, build_outline_chunk_messages, parse_outline, parse_outline_chunk, parse_chapter_plan


def project(brief):
    return SimpleNamespace(title="A Different Book", premise="Deliver the requested book.", notes="", story_brief=StoryBrief(**brief).model_dump())


def run():
    return SimpleNamespace(requested_chapters=2, target_word_count=1200, min_words_per_chapter=400,
                           max_words_per_chapter=800, quality_profile="autonomous")


def test_custom_genre_preserves_unlisted_blend_and_requirements_without_thriller_defaults():
    p = project(dict(genre_profile="custom", genre_description="Literary western with comic folk-tale elements",
                     genre_requirements=["Community restoration", "Dry humor without technical crises"]))
    messages = build_story_bible_messages(p, run())
    assert json.loads(messages[1]["content"])["project"]["story_brief"]["genre_description"] == p.story_brief["genre_description"]
    assert "Community restoration" in messages[0]["content"]
    assert "The ending resolves one central dilemma" not in messages[0]["content"]
    assert genre_profile("future-unlisted-profile").id == "custom"


@pytest.mark.parametrize("kind, book_type, structure", [
    ("fiction", "short-story anthology", "collection"), ("nonfiction", "field handbook", "reference"),
    ("poetry", "sonnet sequence", "collection"), ("drama", "radio play", "episodic"),
])
def test_forms_share_checkpoint_schema_without_inheriting_novel_plot(kind, book_type, structure):
    p = project(dict(genre_profile="custom", content_kind=kind, book_type=book_type, structure=structure,
                     source_material="Manual: record soil moisture daily." if kind == "nonfiction" else ""))
    bible = pipeline._fallback_story_bible(p, run())
    messages = build_story_bible_messages(p, run())
    assert bible.book_spec["book_type"] == book_type
    assert not bible.cast
    assert book_type in messages[0]["content"]
    assert '"logline"' in messages[0]["content"]
    assert "paid-for ending" not in " ".join(bible.act_plan)
    outline = pipeline._fallback_outline_entries(p, bible, 1, 2, 2)
    assert [entry["chapter_number"] for entry in outline] == [1, 2]
    assert not any("trusted ally" in json.dumps(entry) or "public standing" in json.dumps(entry) for entry in outline)


def test_nonfiction_requires_supplied_content_and_retains_sources_through_drafting_and_summary():
    with pytest.raises(ValidationError, match="requires source material"):
        StoryBrief(content_kind="nonfiction")
    with pytest.raises(ValidationError, match="not automatically retrieved"):
        StoryBrief(content_kind="nonfiction", source_material="https://example.com/manual")
    p = project(dict(content_kind="nonfiction", book_type="handbook", structure="instructional",
                     source_material="Source A: the marked gauge has a 10 mL capacity."))
    chapter = SimpleNamespace(chapter_number=1, title="Measuring", content="Use the marked gauge.", summary=None)
    messages = build_chapter_draft_messages(p, run(), chapter, {}, {}, {}, "", ChapterPlan(
        opening_state="Introduce measurement", character_goal="Read the marked gauge", conflict_turn="Avoid unit confusion", ending_hook="Use the scale correctly"))
    assert json.loads(messages[1]["content"])["project"]["story_brief"]["source_material"] == p.story_brief["source_material"]
    assert "Never fabricate research" in messages[0]["content"]
    summary = build_summary_messages(chapter, {}, project=p)
    assert "plain text only" in summary[0]["content"]
    assert "what this chapter actually contains" in summary[0]["content"]


def test_summary_uses_saved_book_sources_after_project_brief_changes():
    p = project(dict(content_kind="nonfiction", book_type="handbook", source_material="Original source: the gauge holds 10 mL."))
    snapshot = dict(book_spec=dict(p.story_brief))
    p.story_brief = StoryBrief(genre_profile="mystery").model_dump()
    chapter = SimpleNamespace(chapter_number=1, title="Measuring", content="The gauge holds 10 mL.", summary=None)
    messages = build_summary_messages(chapter, {}, project=p, story_bible=snapshot)
    assert "Never fabricate research" in messages[0]["content"]
    context = json.loads(messages[1]["content"])
    assert context["project"]["story_brief"]["source_material"] == "Original source: the gauge holds 10 mL."


def test_nonfiction_lint_keeps_unfinished_output_blocking_without_demanding_character_choices():
    bible = {"book_spec": {"content_kind": "nonfiction"}}
    chapter = SimpleNamespace(chapter_number=1, content="The next chapter explains the measuring scale.")
    assert not lint_chapter(chapter, {}, {}, bible, {}, []).needs_repair
    chapter.content += " [TODO add the procedure]"
    assert lint_chapter(chapter, {}, {}, bible, {}, []).blocking_issues


def test_collection_adjudication_checks_its_own_closure_and_shared_canon():
    brief = dict(genre_profile="custom", structure="collection", content_kind="fiction", book_type="anthology")
    review = EditorialReview(reviewed_chapters=[2], plot_coherent=True, canon_consistent=True,
                            prose_clean=True, ending_complete=True, issues=[])
    messages = editor._adjudication_messages("whole_book", {"author_brief": brief, "is_final_chapter": True}, review, {})
    assert "do not force unrelated pieces into one plot" in messages[0]["content"]
    assert "single conflict spanning every piece" in messages[0]["content"]


def test_factual_defects_remain_required_repairs_with_actual_manuscript_evidence():
    raw = dict(reviewed_chapters=[1], plot_coherent=True, canon_consistent=False, prose_clean=True, ending_complete=True,
               issues=[dict(chapter_number=1, category="factual", problem="Source A gives 10 mL, not 20 mL.",
                            evidence="The gauge holds 20 mL.", repair_instruction="Use the source's 10 mL capacity.")])
    review = editor.validate_review(json.dumps(raw), [SimpleNamespace(chapter_number=1, content="The gauge holds 20 mL.")])
    assert not review.passed
    raw["issues"][0]["evidence"] = "An invented quotation."
    with pytest.raises(ValueError, match="actual prose"):
        editor.validate_review(json.dumps(raw), [SimpleNamespace(chapter_number=1, content="The gauge holds 20 mL.")])


def test_invalid_form_controls_are_rejected_instead_of_silently_changing_form():
    with pytest.raises(ValidationError, match="content_kind"):
        StoryBrief(content_kind="not-a-content-kind")
    with pytest.raises(ValidationError, match="structure"):
        StoryBrief(structure="not-a-structure")
    assert "usable conclusion" in contract_text(dict(content_kind="nonfiction"))


def test_saved_book_contract_survives_later_project_brief_changes():
    p = project(dict(genre_profile="custom", content_kind="poetry", book_type="haiku collection", structure="collection"))
    snapshot = dict(p.story_brief)
    p.story_brief = StoryBrief(genre_profile="sci_fi_thriller").model_dump()
    messages = build_chapter_draft_messages(p, run(), SimpleNamespace(chapter_number=1, content=""), {},
        {"book_spec": snapshot}, {}, "", ChapterPlan(opening_state="Winter", character_goal="Stillness", conflict_turn="A thaw", ending_hook="A drop falls"))
    assert "haiku collection" in messages[0]["content"]
    assert json.loads(messages[1]["content"])["project"]["story_brief"] == snapshot


def test_nonfiction_outline_accepts_learning_progress_without_setbacks_or_midpoint_reversals():
    p = project(dict(content_kind="nonfiction", book_type="handbook", source_material="Source: a documented method."))
    bible = pipeline._fallback_story_bible(p, run())
    outline = pipeline._fallback_outline_entries(p, bible, 1, 8, 8)
    for entry in outline:
        entry.pop("chapter_mode")
        entry["outcome_type"] = "win"
    parsed = parse_outline(json.dumps(outline), 8, book_spec=p.story_brief)
    assert all(entry["chapter_mode"] == "essay" for entry in parsed)
    with pytest.raises(ValueError, match="sequentially"):
        parse_outline(json.dumps([outline[0]] * 8), 8, book_spec=p.story_brief)


def test_nonfiction_plan_requires_coverage_and_ending_without_invented_character_choices():
    spec = dict(content_kind="nonfiction", book_type="handbook")
    payload = dict(opening_state="Introduce the procedure", character_goal="Reader learns the record fields",
                   conflict_turn="Distinguish two similar fields", scene_beats=["Explain the fields", "Work an example"], ending_hook="Complete the worked example")
    assert parse_chapter_plan(json.dumps(payload), book_spec=spec).scene_beats
    payload["scene_beats"] = []
    with pytest.raises(ValueError, match="concrete beats"):
        parse_chapter_plan(json.dumps(payload), book_spec=spec)
    from novel_generator.services.prompts import build_chapter_plan_messages
    from novel_generator.services.structured_schema_runtime import extract_schema_marker
    p = project(dict(content_kind="nonfiction", book_type="handbook", source_material="Source: the documented procedure."))
    messages = build_chapter_plan_messages(p, run(), SimpleNamespace(chapter_number=1), {}, {}, {}, "")
    _, schema, _ = extract_schema_marker(messages)
    assert "scene_beats" in schema["required"]
    assert schema["properties"]["scene_beats"]["minItems"] == 1
    assert schema["properties"]["ending_hook"]["minLength"] == 1


def test_outline_decoder_restricts_requested_batch_and_parser_rejects_extra_chapters():
    from novel_generator.services.structured_schema_runtime import extract_schema_marker
    p = project(dict(genre_profile="custom"))
    bible = pipeline._fallback_story_bible(p, run())
    messages = build_outline_chunk_messages(p, run(), bible, start_chapter=2, end_chapter=2, prior_outline=[])
    clean, schema, _ = extract_schema_marker(messages)
    assert schema["minItems"] == schema["maxItems"] == 1
    assert schema["$defs"]["StructuredOutlineEntry"]["properties"]["chapter_number"]["enum"] == [2]
    outline = pipeline._fallback_outline_entries(p, bible, 1, 2, 2)
    with pytest.raises(ValueError, match="expected 2 through 2"):
        parse_outline_chunk(json.dumps(outline), 2, 2, book_spec=p.story_brief)


def test_custom_fiction_does_not_inherit_compulsory_thriller_lint_obligations():
    chapter = SimpleNamespace(chapter_number=1, content="Ada opened the gate. Ben handed her an apricot.")
    assert not lint_chapter(chapter, {}, {}, {"book_spec": {"genre_profile": "custom"}}, {}, []).needs_repair


def test_inapplicable_scores_do_not_force_fictional_repairs_but_real_defects_remain_blocking():
    spec = dict(content_kind="nonfiction", book_type="handbook")
    critique = ChapterCritique(dialogue_tension_score=0, side_character_independence_score=0,
                               irreversibility_score=0, choice_clarity_score=0, revision_required=False)
    feedback = pipeline._combine_chapter_feedback(critique, ChapterLintResult(), book_spec=spec)
    assert not feedback.revision_required
    assert feedback.dialogue_tension_score == 0
    critique.blocking_issues = ["The procedure gives an unsupported capacity."]
    feedback = pipeline._combine_chapter_feedback(critique, ChapterLintResult(), book_spec=spec)
    assert feedback.revision_required and feedback.blocking_issues == critique.blocking_issues


def test_nonfiction_cannot_bypass_source_audit_even_when_general_review_passes(monkeypatch):
    from novel_generator.services.autonomous_contracts import EditorialIssue
    p = project(dict(content_kind="nonfiction", source_material="Manual: the gauge holds 10 mL."))
    target_run = SimpleNamespace(project=p, story_bible={"book_spec": p.story_brief}, quality_profile="autonomous")
    chapter = SimpleNamespace(chapter_number=1, content="The gauge holds 20 mL.")
    calls = []
    def review(session, run, chapters, context, client, scope):
        calls.append(scope)
        issues = [] if scope == "chapter" or "10 mL" in chapter.content else [EditorialIssue(
            chapter_number=1, category="factual", problem="The source says 10 mL.", evidence=chapter.content,
            repair_instruction="Correct 20 mL to the source's 10 mL.")]
        return EditorialReview(reviewed_chapters=[1], plot_coherent=True, canon_consistent=not issues,
                               prose_clean=True, ending_complete=True, issues=issues)
    monkeypatch.setattr(editor, "draft_checkpoint", lambda *args: True)
    monkeypatch.setattr(editor, "_context", lambda *args: {})
    monkeypatch.setattr(editor, "_review", review)
    monkeypatch.setattr(editor, "_length_issues", lambda *args: [])
    repairs = []
    def repair(session, run, ch, ledger, issues, settings, client, phase):
        repairs.extend(issues)
        ch.content = "The gauge holds 10 mL."
    monkeypatch.setattr(editor, "_repair", repair)
    editor.ensure_chapter(None, target_run, chapter, None, None, None)
    assert [issue.category for issue in repairs] == ["factual"]
    assert calls == ["chapter", "source_accuracy", "chapter", "source_accuracy"]


def test_source_audit_constraints_keep_other_checks_separate_and_require_full_coverage():
    from novel_generator.services.structured_schema_runtime import extract_schema_marker
    _, schema, _ = extract_schema_marker([editor._coverage_marker([2], "source_accuracy", paragraph_count=12)])
    assert schema["properties"]["reviewed_chapters"]["const"] == [2]
    assert schema["properties"]["ending_complete"]["const"] is True
    assert schema["$defs"]["EditorialIssue"]["properties"]["category"]["const"] == "factual"
    issue_schema = schema["$defs"]["EditorialIssue"]
    assert "evidence_paragraphs" in issue_schema["required"]
    assert issue_schema["properties"]["evidence"]["const"] == "Source paragraphs"
    assert issue_schema["properties"]["evidence_paragraphs"]["minItems"] == 1
    assert issue_schema["properties"]["evidence_paragraphs"]["items"]["enum"] == list(range(1, 13))


def test_formatted_factual_evidence_attaches_source_for_bounded_repairs():
    from novel_generator.services.local_prose_repair import repair_plan
    text = "> * **Open Loan Count:** 1\n\nSupported information stays.\n\nThe Borrower Card tracks a user's identity and limits."
    payload = dict(reviewed_chapters=[1], plot_coherent=True, canon_consistent=False, prose_clean=True,
        ending_complete=True, issues=[dict(chapter_number=1, category="factual",
            problem="The source specifies only an ID and contact detail.", evidence="Source paragraphs",
            evidence_paragraphs=[1, 3], repair_instruction="Remove the unsupported field and limit claim.")])
    review = editor.validate_review(json.dumps(payload), [SimpleNamespace(chapter_number=1, content=text)])
    assert not review.passed
    plan = repair_plan(text, review.issues)
    assert [text[slice(*span)] for span, _ in plan] == [
        "> * **Open Loan Count:** 1", "The Borrower Card tracks a user's identity and limits."]


def test_assembly_strips_duplicate_markdown_chapter_heading_without_removing_section_headings():
    from novel_generator.services.prompts import sanitize_chapter_content
    assert sanitize_chapter_content("# Chapter 1: The Desk\n\n### Record fields\nExplain the fields.") == "### Record fields\nExplain the fields."
