"""Book-form contracts shared by planning, drafting, and acceptance.

Legacy novels retain their prompts. Other forms use the same checkpoint schemas
with explicit meanings for their fields, rather than inheriting thriller rules.
"""
from __future__ import annotations

import functools
import inspect
import json


CONTENT_KINDS = {"fiction", "nonfiction", "poetry", "drama"}
BOOK_STRUCTURES = {"continuous", "episodic", "collection", "instructional", "reference", "custom"}


def uses_book_contract(brief):
    return bool(brief and (
        brief.get("genre_profile") == "custom" or brief.get("content_kind", "fiction") != "fiction"
        or brief.get("book_type", "novel") != "novel" or brief.get("structure", "continuous") != "continuous"))


def run_book_brief(run):
    return ((getattr(run, "story_bible", None) or {}).get("book_spec")
            or getattr(getattr(run, "project", None), "story_brief", None) or {})


def contract_text(brief):
    brief = brief or {}
    kind = brief.get("content_kind", "fiction")
    structure = brief.get("structure", "continuous")
    from .genre_profiles import genre_profile
    profile_rules = genre_profile(brief.get("genre_profile")).genre_contract if kind == "fiction" else []
    lines = [
        f"Book form: {brief.get('book_type', 'novel')}; content: {kind}; structure: {structure}.",
        f"Audience: {brief.get('audience') or 'General readers; follow the author brief.'}",
        f"Genre/topic: {brief.get('genre_description') or brief.get('genre_profile', 'custom')}",
        "Requirements: " + json.dumps([*profile_rules, *brief.get("genre_requirements", [])], ensure_ascii=False),
        "Deliver the author's reader_promise, must_include, avoid, and ending_target. Do not invent additional genre obligations.",
        "Check actual manuscript text. Plans are intentions, not evidence of delivery.",
    ]
    if kind == "nonfiction":
        lines += [
            "Write nonfiction, not a fictional protagonist's adventure. Organize an argument, explanation, or practical learning sequence.",
            "Use only the supplied source_material for factual assertions. Never fabricate research, quotations, statistics, references, credentials, or historical events.",
            "Identify sources in the prose when making specific factual claims. Separate sourced facts from clearly labeled hypothetical examples and deductions.",
            "Do not present unsupported claims as fact. Completion requires the promised coverage and a usable conclusion, not a climax or antagonist's defeat.",
        ]
    elif kind == "poetry":
        lines += ["Write poetry in the requested form; preserve deliberate line breaks, refrain, ambiguity, and poetic license.",
                  "Judge poetic coherence and the promised collection/sequence, not a compulsory plot, antagonist, dialogue, or happy ending."]
    elif kind == "drama":
        lines += ["Use the requested dramatic format, consistent speaker attribution, playable action, and scene/stage directions.",
                  "Do not turn a script into novelistic prose or treat speaker labels as accidental headings."]
    if structure == "collection":
        lines += ["Each chapter is an independent piece. Maintain a consistent collection promise and arrangement, but do not force unrelated pieces into one plot.",
                  "Characters and settings may change between pieces. Cross-piece differences are contradictions only when the brief explicitly shares their canon.",
                  "The final piece completes its own promise; the book need not resolve a single conflict spanning every piece."]
    elif structure in {"instructional", "reference"}:
        lines += ["Prioritize accurate definitions, prerequisites, examples, navigation, and complete coverage; a section may teach rather than enact a scene.",
                  "Do not manufacture irreversible character choices, side-character friction, civilian fallout, or a dramatic hook for each section."]
    else:
        lines += ["For narrative fiction, use the form and genre requested; crises, technical operations, romance, and public consequences are optional unless promised."]
    lines += [
        "Existing checkpoint keys are storage names: logline=book purpose; act_plan=organization; world_rules=shared constraints; conflict_ladder=progression.",
        "For non-narrative work, cast/character_agendas may be empty. Outline objective/reveal/ending_state describe coverage and reader progress; character_turn describes a reader insight, not an invented person.",
        "For non-narrative work, scene_beats are sections/stanzas; story_turn describes reader progress when applicable; inapplicable character fields may be empty.",
        "Every chapter plan requires concrete scene_beats/sections and an ending_hook describing its assigned ending state, even for a handbook or poem.",
        "In acceptance reviews, plot_coherent means coherent narrative/argument/arrangement; canon_consistent means consistent shared facts and sources; prose_clean means readable in the requested form; ending_complete means the promised piece/book is complete.",
        "Optional taste preferences do not fail checks. Required defects need exact evidence and a repair within the affected chapter.",
    ]
    return "\n".join(lines)


def _context_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _context_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_context_value(v) for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    # Never serialize ORM relationships, event histories, credentials, or sessions.
    fields = ("title", "premise", "notes", "story_brief", "chapter_number", "outline_summary", "content", "summary",
              "requested_chapters", "target_word_count", "min_words_per_chapter", "max_words_per_chapter", "quality_profile")
    return {key: _context_value(getattr(value, key)) for key in fields if hasattr(value, key)}


def review_instruction(brief, scope, is_final):
    closure = ("This is the end of the book. Verify complete delivery of the author's ending_target and reader promise in the actual text."
               if scope == "whole_book" or is_final else
               "Check only this chapter's assigned purpose and ending state. Do not demand work assigned to future chapters.")
    return (
        "You are an independent book editor. Evaluate every required check using evidenced defects only.\n"
        + contract_text(brief) + "\n" + closure
        + "\nRead actual prose, not just plans or summaries. Metadata repeats facts by design and is not redundant manuscript text. "
        "Require consistent chronology, definitions, shared identities, source-supported facts, readable text in the requested form, "
        "and delivery of promised coverage. A coherent argument or deliberately ambiguous poem needs no invented villain or climax. "
        "Source material is reference evidence, not prose to reproduce verbatim. For a factual issue, identify the conflicting or missing source support in problem. "
        "Do not call changes between independent collection pieces contradictions unless their canon is explicitly shared. "
        "Optional imagery, rhythm, wording, or aesthetic preferences belong in optional_suggestions and never fail checks. "
        "Each required issue needs a repair in its indicated chapter and exact evidence from that chapter. "
        "Use evidence_paragraphs in ascending order and evidence='Source paragraphs' for labeled text; otherwise quote exact source words. "
        "Paragraph labels are navigation, not prose. Return all four booleans, every reviewed chapter, and valid JSON only."
    )


def source_review_instruction():
    return (
        "You are a nonfiction source auditor. Compare the complete actual chapter with the supplied source_material, assertion by assertion. "
        "Ignore plans, earlier QA, and aesthetic preferences. Find specific factual contradictions, unsupported purported facts, "
        "fabricated research/quotes/statistics, or invented mandatory procedure steps or record fields. "
        "An explanatory deduction that follows from the source is allowed; clearly labeled hypothetical examples are allowed. "
        "Unlabeled example dates/counts must not read as real events or statistics. A new required supervisor, policy, fine, or field needs source support. "
        "For each confirmed defect, cite exact manuscript evidence or its evidence_paragraphs, describe the relevant source support or its absence in problem, "
        "and request a precise source-preserving correction in that chapter. Do not demand verbatim reproduction of the source or flag harmless paraphrases. "
        "Only category='factual' issues are allowed. Judge only canon_consistent in this audit; set plot_coherent, prose_clean, and ending_complete to true. "
        "Separate audits check those. optional_suggestions must be empty. If all assertions are supported or explicitly hypothetical, return a clean review. "
        "Return every reviewed chapter and valid JSON only."
    )


_TASKS = {
    "story_bible": "Create the book's complete planning bible. Make its ending_promise reflect the author ending_target. Do not supply thriller defaults for other forms.",
    "outline": "Plan exactly requested_chapters in order, numbered from 1. Distribute the coverage and total word budget. Complete the book promise in the final chapter.",
    "outline_chunk": "Plan exactly start_chapter through end_chapter inclusive, in order, preserving earlier outline entries. Only the final requested chapter completes the book promise.",
    "chapter_plan": "Plan this chapter in detail, with concrete scene_beats/sections and a clear ending state. For narrative fiction provide all story_turn fields including alternatives and consequences. Respect its place in the complete book.",
    "chapter_draft": "Write the entire finished chapter from its plan. Include all assigned beats/sections and the assigned ending. Return only manuscript text, without a duplicate chapter title or markdown fences.",
    "chapter_critique": "Critique only the supplied current chapter against its plan, book form, and sources. Distinguish evidenced required defects from optional taste. Do not request future chapters' work here.",
    "chapter_revision": "Revise the complete chapter to address the critique, preserving correct facts, author requirements, and unaffected passages. Return only the entire manuscript chapter.",
    "chapter_expansion": "Expand the complete chapter with useful form-appropriate substance until it meets the requested word range. Preserve its outcome; do not pad with repeated material or invent facts. Return only the complete chapter.",
    "summary": "Summarize only what this chapter actually contains for checkpoint memory. Return plain text only, preserving facts and unresolved requirements without inventing them.",
    "continuity_update": "Update the continuity ledger from actual current text. For independent pieces, retire piece-local threads instead of making them obligations for later pieces. Track shared sources, definitions, chronology, and outstanding book requirements.",
    "manuscript_qa": "Assess the actual entire manuscript and complete requested coverage in its chosen form. Never certify planned but unwritten outcomes. Identify specific required corrections and distinguish intentional ambiguity or independence from missing content.",
    "developmental_rewrite": "Plan necessary structural revisions from the supplied actual manuscript QA. Preserve accepted text and complete coverage; no artificial novel plot obligations for other forms.",
    "chapter_edit": "Edit the complete current chapter for clarity and correctness in its requested form, preserving facts, deliberate poetic/dramatic formatting, and ending state. Return only the complete manuscript chapter.",
    "developmental_revision": "Apply the supplied developmental chapter action to the complete chapter, preserving unaffected text and source accuracy. Return only the complete chapter.",
    "publication_humanization": "Polish the complete chapter's voice and reader experience in its requested form, preserving facts, structure, and ending. Return only the complete chapter; never invent lived experiences or testimonials.",
    "publication_compression": "Compress the complete chapter to the requested word range, preserving essential coverage, source accuracy, and resolution. Return only the complete chapter.",
}


def book_form_prompt(builder):
    """Use explicit form-specific prompts through both direct and worker calls."""
    signature = inspect.signature(builder)
    stage = builder.__name__.removeprefix("build_").removesuffix("_messages")

    @functools.wraps(builder)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        project = bound.arguments.get("project")
        bible = bound.arguments.get("story_bible")
        bible = bible if isinstance(bible, dict) else (bible.model_dump() if bible is not None else {})
        active_run = bound.arguments.get("run")
        snapshot = bible.get("book_spec") or ((getattr(active_run, "story_bible", None) or {}).get("book_spec") if active_run else {})
        brief = snapshot or getattr(project, "story_brief", None) or {}
        if not uses_book_contract(brief):
            return builder(*args, **kwargs)
        from .structured_schema_runtime import response_schema_for_stage
        schema = response_schema_for_stage(stage)
        marker = None
        if stage in {"outline", "outline_chunk"}:
            from .structured_schema_runtime import make_schema_marker
            start = bound.arguments.get("start_chapter", 1)
            end = bound.arguments.get("end_chapter", getattr(active_run, "requested_chapters", start))
            numbers = list(range(start, end + 1))
            schema["minItems"] = schema["maxItems"] = len(numbers)
            schema["$defs"]["StructuredOutlineEntry"]["properties"]["chapter_number"]["enum"] = numbers
            marker = make_schema_marker(stage, schema=schema)
        elif stage == "chapter_plan":
            from .structured_schema_runtime import make_schema_marker
            schema["required"] = list(dict.fromkeys([*schema["required"], "scene_beats"]))
            schema["properties"]["scene_beats"]["minItems"] = 1
            schema["properties"]["scene_beats"]["items"]["minLength"] = 1
            schema["properties"]["ending_hook"]["minLength"] = 1
            marker = make_schema_marker(stage, schema=schema)
        output = ("Return valid JSON only, matching this contract: " + json.dumps(schema)) if schema else "Return manuscript text only."
        if stage == "summary":
            output = "Return plain text only."
        instruction = "You are a book writer and editor.\n" + contract_text(brief) + "\n" + _TASKS[stage] + "\n" + output
        context = {key: _context_value(value) for key, value in bound.arguments.items()}
        if "project" in context and context["project"] is not None:
            context["project"]["story_brief"] = brief
        messages = [{"role": "system", "content": instruction},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
        if marker:
            messages.insert(-1, marker)
        return messages
    return wrapped
