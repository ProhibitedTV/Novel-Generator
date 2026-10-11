"""Bounded, checkpointed editorial repair with a fail-closed manuscript acceptance gate."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from ..models import Artifact
from ..repositories import record_event
from .autonomous_contracts import EditorialIssue, EditorialReview
from .context_memory import compile_memory_packet
from .continuity_lifecycle import LIVE_SNAPSHOT_FIELDS, apply_continuity_lifecycle
from .ending_debt import compile_ending_debt_audit
from .prompts import extract_json_payload, parse_continuity_update
from .book_specs import contract_text, uses_book_contract, run_book_brief, review_instruction, source_review_instruction


class AutonomousQualityError(RuntimeError):
    """The manuscript cannot be certified by the configured automatic editorial checks."""


def enabled(run: Any) -> bool:
    return getattr(run, "quality_profile", "") == "autonomous"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _text(value: str) -> str:
    return " ".join(value.split())


def _grounded_evidence(evidence: str, source: str) -> bool:
    quote = _text(evidence)
    if quote and quote in source:
        return True
    # Models commonly reformat dialogue quotation marks, including a closing
    # quote at the end of a partial excerpt. Preserve all other characters and
    # word order; this is punctuation normalization, never fuzzy word matching.
    quotation_marks = str.maketrans("", "", "\"'\u2018\u2019\u201c\u201d")
    quote = _text(quote.translate(quotation_marks))
    source = _text(source.translate(quotation_marks))
    if quote and quote in source:
        return True
    # Editorial quotations may omit intervening text. Every retained excerpt must
    # still occur verbatim, in order; never use fuzzy matching or repair the source.
    pieces = [part.strip() for part in re.split(r"\s*(?:\[\s*(?:\.{3}|\u2026)\s*\]|\.{3}|\u2026)\s*", quote) if part.strip()]
    if not pieces or any(len(piece.split()) < 2 for piece in pieces) or sum(len(piece.split()) for piece in pieces) < 6:
        return False
    cursor = 0
    for piece in pieces:
        position = source.find(piece, cursor)
        if position < 0:
            return False
        cursor = position + len(piece)
    return True


def _paragraphs(text):
    return [paragraph for paragraph in re.split(r"\n\s*\n", text.strip()) if paragraph.strip()]


def _numbered_prose(text, chapter_number):
    return "\n\n".join(f"[Chapter {chapter_number}, paragraph {index}]\n{paragraph}"
                       for index, paragraph in enumerate(_paragraphs(text), 1))


def _coverage_marker(chapter_numbers, scope=None, *, paragraph_count=None):
    from .structured_schema_runtime import make_schema_marker, response_schema_for_stage
    schema = response_schema_for_stage("autonomous_review")
    schema["properties"]["reviewed_chapters"]["const"] = sorted(chapter_numbers)
    schema["$defs"]["EditorialIssue"]["properties"]["chapter_number"]["enum"] = sorted(chapter_numbers)
    definition = schema["$defs"]["EditorialIssue"]
    definition["required"] = list(dict.fromkeys([*definition["required"], "repair_scope"]))
    if scope in {"chapter", "final_chapter", "adjacent_chapters", "source_accuracy"}:
        # Complete prose in these scopes is always numbered. References also
        # anchor omission diagnoses, which cannot quote an absent sentence.
        issue = schema["$defs"]["EditorialIssue"]
        issue["required"] = list(dict.fromkeys([*issue["required"], "evidence_paragraphs"]))
        issue["properties"]["evidence"]["const"] = "Source paragraphs"
        issue["properties"]["evidence_paragraphs"]["minItems"] = 1
        if paragraph_count is not None:
            issue["properties"]["evidence_paragraphs"]["items"]["enum"] = list(range(1, paragraph_count + 1))
    if scope == "adjacent_chapters":
        for check in ("prose_clean", "ending_complete"):
            schema["properties"][check]["const"] = True
        schema["$defs"]["EditorialIssue"]["properties"]["category"]["enum"] = ["continuity", "character", "causality"]
        schema["properties"]["optional_suggestions"]["maxItems"] = 0
    if scope == "source_accuracy":
        for check in ("plot_coherent", "prose_clean", "ending_complete"):
            schema["properties"][check]["const"] = True
        issue = schema["$defs"]["EditorialIssue"]
        issue["properties"]["category"]["const"] = "factual"
        schema["properties"]["optional_suggestions"]["maxItems"] = 0
    return make_schema_marker("autonomous_review", schema=schema)


def _inline_paragraph_references(evidence, chapter_number):
    # Local reviewers vary the spelling of navigation labels. Interpret only
    # explicit labels; the referenced source is attached by validate_review.
    pattern = re.compile(
        r"\[Chapter\s+(?P<chapter>\d+),\s*paragraph\s+(?P<chapter_para>\d+)\]"
        r"|(?:\(|\[)\s*(?:P|Para\.?|Paragraph)\s+(?P<wrapped>\d+)\s*(?:\)|\])"
        r"|(?:\(|\[)P\s*(?P<short>\d+)(?:\)|\])"
        r"|\b(?:Paragraph|Para\.?)\s+(?P<label>\d+)\b", re.IGNORECASE)
    references = []
    for match in pattern.finditer(evidence):
        if match.group("chapter") and int(match.group("chapter")) != chapter_number:
            raise ValueError("Evidence paragraph label belongs to a different chapter.")
        number = int(next(match.group(key) for key in ("chapter_para", "wrapped", "short", "label") if match.group(key)))
        if number not in references:
            references.append(number)
    return references


def validate_review(raw: str, chapters: list[Any]) -> EditorialReview:
    payload = extract_json_payload(raw)
    # Validate all required checks and defects strictly. Optional advice must
    # not invalidate those checks merely because its quotation is unusable.
    optional = payload.get("optional_suggestions", []) if isinstance(payload, dict) else []
    review = EditorialReview.model_validate({**payload, "optional_suggestions": []} if isinstance(payload, dict) else payload)
    expected = sorted(chapter.chapter_number for chapter in chapters)
    if sorted(review.reviewed_chapters) != expected:
        raise ValueError(f"Review must cover exactly chapters {expected}, without duplicates.")
    source = {chapter.chapter_number: _text(chapter.content or "") for chapter in chapters}
    paragraphs = {chapter.chapter_number: _paragraphs(chapter.content or "") for chapter in chapters}
    def validate_evidence(issue):
        if not issue.evidence_paragraphs and not _grounded_evidence(issue.evidence, source.get(issue.chapter_number, "")):
            # Some local models serialize explicit source labels inside evidence
            # instead of its dedicated array. Treat those as references, never
            # as fuzzy quotations; the source text is still attached below.
            issue.evidence_paragraphs = _inline_paragraph_references(issue.evidence, issue.chapter_number)
        if issue.evidence_paragraphs:
            references = issue.evidence_paragraphs
            available = paragraphs.get(issue.chapter_number, [])
            if references != sorted(set(references)) or any(index < 1 or index > len(available) for index in references):
                raise ValueError("Evidence paragraph references must be valid, unique, and in source order for the indicated chapter.")
            issue.evidence = " [...] ".join(available[index - 1] for index in references)
            return
        if issue.chapter_number not in source or not _grounded_evidence(issue.evidence, source[issue.chapter_number]):
            raise ValueError(f"Issue evidence must quote chapter {issue.chapter_number}'s actual prose, not its outline or summary. Unmatched quotation: {issue.evidence[:300]!r}")
    for issue in review.issues:
        validate_evidence(issue)
    if not isinstance(optional, list):
        review._rejected_optional_suggestions.append({"error": "Optional suggestions must be a list.", "suggestion": optional})
        optional = []
    for row in optional:
        try:
            suggestion = EditorialIssue.model_validate(row)
            validate_evidence(suggestion)
            review.optional_suggestions.append(suggestion)
        except ValueError as exc:
            review._rejected_optional_suggestions.append({"error": str(exc), "suggestion": row})
    return review


def validate_chapter_repair_scope(review):
    for issue in review.issues:
        if re.search(r"\b(?:next|following) chapter (?:must|should|needs? to)\b", issue.repair_instruction, re.IGNORECASE):
            raise ValueError("Chapter review requested work in a future chapter. Judge this chapter against its assigned ending state; report only repairs to this chapter, not advice to the next chapter.")


def _record(session, run, kind: str, payload: dict) -> None:
    record_event(session, run, kind, payload)
    session.commit()
    # record_event inserts by run_id; it does not append to an already-loaded relationship.
    session.expire(run, ["events"])


def _events(run, kind: str):
    return [event.payload for event in run.events if event.event_type == kind]


def final_checkpoint(run):
    if not enabled(run):
        return None
    saved = _events(run, "autonomous_final_checkpoint")
    if saved:
        return saved[-1]
    # Runs made before explicit checkpoints already record entry into final reconciliation.
    if _events(run, "autonomous_metadata_refreshed"):
        return {"qa_report": {}}
    return None


def checkpoint_final_stage(session, run, qa_report, **editorial_exports):
    if enabled(run):
        _record(session, run, "autonomous_final_checkpoint", {
            "message": "Saved final editorial checkpoint; resume will recheck this text without repeating drafting.",
            "qa_report": qa_report.model_dump(), **editorial_exports,
        })


def _pipeline():
    from . import pipeline
    return pipeline


def _context(run, chapter, ledger) -> dict:
    previous = [item for item in run.chapters if item.chapter_number < chapter.chapter_number and item.content]
    following = [item for item in run.chapters if item.chapter_number > chapter.chapter_number and item.content]
    outline = list(run.outline or [])
    nearby = [item for item in outline if abs(item["chapter_number"] - chapter.chapter_number) <= 1]
    if outline and outline[-1] not in nearby:
        nearby.append(outline[-1])
    # Preserve all literal current prose. Only supporting history is compacted.
    return {
        "author_brief": run_book_brief(run),
        "premise": run.project.premise,
        "story_bible": run.story_bible,
        "chapter_number": chapter.chapter_number,
        "total_chapters": run.requested_chapters,
        "is_final_chapter": chapter.chapter_number == run.requested_chapters,
        "chapter_word_range": [run.min_words_per_chapter, run.max_words_per_chapter],
        "nearby_outline_and_finale": nearby,
        "state_before_chapter": compile_memory_packet(ledger, focus=nearby, max_chars=10000).payload,
        "previous_chapter_ending": (previous[-1].content or "")[-3000:] if previous else "",
        "following_chapter_opening": (following[0].content or "")[:3000] if following else "",
        "actual_prose": chapter.content or "",
    }


def _check_context(messages: list[dict], client, provider_name: str) -> None:
    from .context_runtime import _configured_context_tokens
    from .provider_controls import extract_ollama_controls_marker
    limit = _configured_context_tokens(client, provider_name)
    _, controls = extract_ollama_controls_marker(messages)
    reserve = 8192 if controls.get("think") is True else 6144
    if limit and math.ceil(sum(len(item["content"]) for item in messages) / 4) + reserve > limit:
        raise AutonomousQualityError("Automatic editorial context exceeds the configured model window. Increase context or reduce chapter size; no prose was silently truncated.")


def _adjudication_messages(scope, context, review, controls):
    # Passing the first review's flags and desired repair made the verifier copy
    # its answer. Supply allegations, not an answer-shaped example to imitate.
    source = {key: value for key, value in context.items() if key not in {
        "legacy_qa_concerns_to_verify_against_evidence", "ending_debt_to_verify_against_actual_prose",
    }}
    allegations = [{key: getattr(issue, key) for key in
        ("chapter_number", "category", "problem", "evidence", "evidence_paragraphs")}
        for issue in review.issues]
    instruction = (
        "You are independently verifying editorial allegations, not rewriting a previous review. "
        "Read the supplied actual prose before deciding whether each allegation is true. Search the ENTIRE supplied source "
        "for counterevidence, including intervening paragraphs and scene changes. A character can leave a place and later return; "
        "travel between scenes is not inherently contradictory. Receiving an object in an intervening paragraph can explain later possession. "
        "A quotation establishes that words occur, not that a defect exists. Reject a claim when the source already supplies the requested "
        "cause, action, transfer, or consequence. Do not require the same fact to be restated in another paragraph. "
        "Keep genuine incompatible facts, impossible causes, broken grammar, duplicate passages, or absent promised outcomes blocking. "
        "Do not invent replacement defects to justify a rejected allegation. Optional aesthetic improvements belong in optional_suggestions. "
        "Independently decide ALL four booleans and provide evidence-grounded issues only for confirmed defects. "
        "Use evidence_paragraphs from the indicated chapter when source labels are available, with evidence='Source paragraphs'; "
        "otherwise quote exact source words. Return valid JSON only, matching this contract: "
        + json.dumps(EditorialReview.model_json_schema())
    )
    if scope == "source_accuracy":
        instruction += "\n" + source_review_instruction()
    elif scope == "adjacent_chapters":
        instruction += (
            " Only compare factual continuity, character identity, and causes across these complete chapters. "
            "Only continuity, character, and causality issues are allowed. Set prose_clean and ending_complete to true; "
            "separate audits cover those checks. optional_suggestions must be empty. Explain both incompatible claims and their chapters."
        )
    elif scope == "whole_book" or context.get("is_final_chapter"):
        if uses_book_contract(context.get("author_brief")):
            instruction += " This is the end of the book. Verify the promised ending_target and complete coverage in the requested book form; there is no next chapter."
        else:
            instruction += (
                " This is the end of the book: require the author's central resolution, cost, and promised aftermath on page. "
                "If they are already depicted, do not demand a longer or differently worded depiction. Beginning a new life does not "
                "require completing a lifetime project. Judge current prose, not a stale outline or QA diagnosis."
            )
    else:
        instruction += (
            " Judge this chapter's assigned scene turn. A non-final setup may end on discovery, decision, or threat; "
            "do not require events assigned to future chapters."
        )
    if uses_book_contract(context.get("author_brief")):
        instruction += "\nThe following book-form contract governs interpretation of the checks and closure requirements:\n" + contract_text(context["author_brief"])
    messages = [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(
        {"scope": scope, **source, "proposed_review_to_verify": allegations}, ensure_ascii=False)}]
    if controls:
        from .provider_controls import make_ollama_controls_marker
        messages.insert(0, make_ollama_controls_marker(**controls))
    messages.insert(-1, _coverage_marker(review.reviewed_chapters, scope,
        paragraph_count=len(_paragraphs(context["actual_prose"])) if "actual_prose" in context else None))
    return messages


def _review(session, run, chapters, context: dict, client, scope: str) -> EditorialReview:
    pipeline = _pipeline()
    pipeline._ensure_not_canceled(session, run)
    provider, model = pipeline._resolve_stage_route(client, run, "autonomous_review")
    adjudicator_provider, adjudicator_model = pipeline._resolve_stage_route(client, run, "autonomous_revision")
    controls = {}
    adjudication_controls = {}
    if provider == "ollama" or adjudicator_provider == "ollama":
        from ..settings import get_settings
        config = getattr(client, "settings", None) or get_settings()
    if scope in {"adjacent_chapters", "source_accuracy"} and provider == "ollama":
        if getattr(config, "ollama_continuity_think", None) is not None:
            controls["think"] = config.ollama_continuity_think
        if getattr(config, "ollama_continuity_temperature", None) is not None:
            controls["temperature"] = config.ollama_continuity_temperature
    if adjudicator_provider == "ollama":
        if getattr(config, "ollama_adjudication_think", None) is not None:
            adjudication_controls["think"] = config.ollama_adjudication_think
        if getattr(config, "ollama_adjudication_temperature", None) is not None:
            adjudication_controls["temperature"] = config.ollama_adjudication_temperature
    fingerprint = _hash({"contract": 19, "context": context, "provider": provider, "model": model, "scope": scope, "controls": controls,
                         "adjudication_controls": adjudication_controls,
                         "adjudicator": [adjudicator_provider, adjudicator_model]})
    for saved in reversed(_events(run, "autonomous_review_completed")):
        if saved.get("fingerprint") == fingerprint:
            return validate_review(json.dumps(saved["review"]), chapters)
    instruction = (
        "You are an independent fiction editor. Return valid JSON only, matching this contract: "
        + json.dumps(EditorialReview.model_json_schema())
        + "\nJudge actual prose, never assume planned events happened. Check chronology, who knows what, "
        "character identity and occupation, dialogue speaker attribution, world rules, causal transitions, "
        "repetition, grammatical errors, and payoffs occurring in their assigned chapters. "
        "Summaries and metadata repeat story facts by design; they are not manuscript prose. "
        "Count prose repetition only within actual manuscript passages, not across reference fields. "
        "For non-final chapter reviews, ending_complete means the assigned scene turn resolves, not the whole book. "
        "A setup chapter may end on a discovery, decision, or unresolved threat when that is its assigned ending state. "
        "Do not demand investigation, resolution, or consequences assigned to later chapters. Every repair must be "
        "actionable within the chapter being reviewed; advice for the next chapter is not a defect in this chapter. "
        "For the final chapter or a whole-book review, it means the central conflict and author ending promise actually resolve "
        "with consequences and aftermath in the final prose. Do not require every subplot to end happily. "
        "Report concrete defects that require correction, not optional aesthetic preferences or suggestions "
        "to add more sensory detail. A coherent passage need not match your personal style. "
        "Put optional wording, imagery, pacing, or subtext preferences in optional_suggestions, never issues. "
        "A passage you would phrase differently is not broken prose. prose_clean is true when the prose is "
        "grammatical and readable without materially redundant passages, even if optional refinements remain. "
        "Evaluate all four booleans using required defects only; optional_suggestions never fail a check. "
        "Do not invent a physical restriction, required backstory, or author requirement to justify a preference. "
        "A quotation proves that words appear in the manuscript; it does not by itself prove they are defective. "
        "A causality defect requires a missing or contradictory cause, not a wish for a slower or more dramatic delivery. "
        "An unresolved payoff requires a promised event or consequence that is actually absent, not a preference for a different concluding image. "
        "Each failed boolean must be supported by a relevant issue: a stylistic preference does not mean "
        "the ending is incomplete. Do not invent issues to fill the list. Each issue must include an exact short quotation from "
        "the indicated chapter's actual prose and a concrete repair instruction. Missing events can be "
        "evidenced by quoting the ending that omits them. Distinguish discussion or foreshadowing from "
        "actually enacting a future payoff. All four checks and the complete reviewed_chapters list are required."
    )
    if scope == "whole_book" or context.get("is_final_chapter"):
        instruction += (
            "\nTHIS IS THE END OF THE BOOK. THERE IS NO NEXT CHAPTER. Judge closure against the "
            "author_brief ending_target and the premise, even if the outline only describes setup. "
            "An ending that merely discovers evidence or sets up a confrontation fails if the brief "
            "requires a resolved confrontation and aftermath. Quote the actual stopping point and "
            "request the missing resolution as an unresolved_payoff issue."
        )
    instruction += (
        "\nActual prose is labeled by chapter and paragraph. When labels are available, use evidence_paragraphs: give the "
        "supporting paragraph numbers in ascending order and set evidence to 'Source paragraphs'. "
        "The application will attach their exact text; do not copy or reconstruct dialogue. Numbers must belong to the issue's chapter. "
        "If a reference excerpt has no paragraph labels, use an exact short quotation instead. "
        "Paragraph labels are navigation aids, not manuscript text. Do not invent or paraphrase quotations."
    )
    if scope == "adjacent_chapters":
        # Chapter and book reviews cover prose and closure. Give this audit one
        # factual task instead of burying chronology among aesthetic decisions.
        instruction = (
            "You are a book continuity auditor. Compare factual assertions in TWO complete chapters, in book order. "
            "Find incompatible claims about the SAME event, date, person, place, or physical object. Read the actual passages, not summaries. "
            "Relative elapsed time is measured from its own scene: a later scene can correctly give a larger elapsed interval for the same past event. "
            "A scene change or later reflection is not a chronology contradiction. Report definite conflicting facts or impossible causal transitions, "
            "not optional clarifications, aesthetic preferences, or invented restrictions. Only continuity, character, or causality issues are allowed. "
            "canon_consistent is false for conflicting facts; plot_coherent is false for impossible or contradictory causes. "
            "Set prose_clean and ending_complete to true because separate audits cover them. optional_suggestions must be empty. "
            "Provide exact evidence from the chapter you ask to repair, preferably its numbered evidence_paragraphs and evidence='Source paragraphs'. "
            "Explicitly describe BOTH conflicting statements and their chapters in problem. Return valid JSON matching this contract: "
            + json.dumps(EditorialReview.model_json_schema())
            + " Perform one comparison pass. If no demonstrated contradiction remains, return the clean review immediately. "
            "Do not revisit a settled calculation or invent another requirement."
        )
    review_context = dict(context)
    if "actual_prose" in context:
        review_context["actual_prose"] = _numbered_prose(context["actual_prose"], context["chapter_number"])
    if "final_chapter_actual_prose" in context:
        review_context["final_chapter_actual_prose"] = _numbered_prose(context["final_chapter_actual_prose"], max(chapter.chapter_number for chapter in chapters))
    if uses_book_contract(context.get("author_brief")):
        if scope == "adjacent_chapters":
            instruction += "\nThe following book-form contract governs interpretation of the checks and closure requirements:\n" + contract_text(context["author_brief"])
        else:
            instruction = review_instruction(context["author_brief"], scope, context.get("is_final_chapter")) + "\n" + json.dumps(EditorialReview.model_json_schema())
    if scope == "source_accuracy":
        instruction = source_review_instruction() + "\n" + json.dumps(EditorialReview.model_json_schema())
    messages = [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps({"scope": scope, **review_context}, ensure_ascii=False)}]
    if controls:
        from .provider_controls import make_ollama_controls_marker
        messages.insert(0, make_ollama_controls_marker(**controls))
    messages.insert(-1, _coverage_marker([chapter.chapter_number for chapter in chapters], scope,
        paragraph_count=len(_paragraphs(chapters[0].content or "")) if len(chapters) == 1 else None))
    _check_context(messages, client, provider)
    run.current_step = "autonomous_review"
    run.current_chapter = chapters[0].chapter_number if len(chapters) == 1 else None
    session.commit()
    def parse_review(raw):
        try:
            review = validate_review(raw, chapters)
            if scope in {"chapter", "final_chapter"}:
                validate_chapter_repair_scope(review)
            if scope == "adjacent_chapters" and (not review.prose_clean or not review.ending_complete or
                any(issue.category not in {"continuity", "character", "causality"} for issue in review.issues)):
                raise ValueError("Chapter-pair audits compare factual continuity, character identity, and causes only. Prose and ending checks belong to the separate chapter and whole-book audits.")
            if scope == "source_accuracy" and (not all((review.plot_coherent, review.prose_clean, review.ending_complete))
                or any(issue.category != "factual" for issue in review.issues) or review.optional_suggestions):
                raise ValueError("Source audits judge factual support only; keep other checks true and report only factual issues without optional suggestions.")
            if review._rejected_optional_suggestions:
                _record(session, run, "autonomous_advisory_rejected", {
                    "message": "Discarded unsupported optional advice; required editorial checks remain validated.",
                    "scope": scope, "fingerprint": fingerprint,
                    "rejected_suggestions": review._rejected_optional_suggestions,
                })
            return review
        except ValueError as exc:
            _record(session, run, "autonomous_review_rejected", {
                "message": "Automatic review failed validation and requires correction.",
                "scope": scope, "error": str(exc), "raw_review": raw[:20000],
                "fingerprint": fingerprint,
            })
            raise

    review = pipeline._generate_structured_output(
        session, run, client, provider, model, lambda: messages,
        parse_review, "automatic editorial review", "autonomous_review",
        run.current_chapter,
    )
    affected = {issue.chapter_number for issue in review.issues}
    prior_repairs = [item for item in _events(run, "autonomous_repair_started")
                     if item.get("chapter_number") in affected]
    if review.issues and scope in {"chapter", "final_chapter", "whole_book", "adjacent_chapters", "source_accuracy"} and len(prior_repairs) >= 2:
        # A separate decision pass checks a repeatedly revised chapter against
        # the actual contract, rather than endlessly following new preferences.
        proposed = review.model_dump()
        adjudication = _adjudication_messages(scope, review_context, review, adjudication_controls)
        _check_context(adjudication, client, adjudicator_provider)
        review = pipeline._generate_structured_output(
            session, run, client, adjudicator_provider, adjudicator_model, lambda: adjudication,
            parse_review, "editorial defect adjudication", "autonomous_review", run.current_chapter,
        )
        _record(session, run, "autonomous_review_adjudicated", {
            "message": "Rechecked proposed defects against revised prose and assigned chapter scope.",
            "chapter_number": run.current_chapter, "proposed_review": proposed,
            "scope": scope, "reviewed_chapters": [chapter.chapter_number for chapter in chapters],
            "provider_name": adjudicator_provider, "model_name": adjudicator_model,
            "controls": adjudication_controls,
            "review": review.model_dump(), "fingerprint": fingerprint,
        })
    _record(session, run, "autonomous_review_completed", {
        "message": f"Automatic {scope} review {'passed' if review.passed else 'requested repairs'}.",
        "scope": scope, "fingerprint": fingerprint, "review": review.model_dump(),
        "provider_name": provider, "model_name": model,
        "controls": controls,
    })
    return review


def _length_issues(run, chapter) -> list[EditorialIssue]:
    count = len((chapter.content or "").split())
    if not (chapter.content or "").strip():
        raise AutonomousQualityError(f"Chapter {chapter.chapter_number} has no prose.")
    if run.min_words_per_chapter <= count <= run.max_words_per_chapter:
        return []
    return [EditorialIssue(
        chapter_number=chapter.chapter_number, category="length",
        problem=f"Chapter is {count} words; required range is {run.min_words_per_chapter}-{run.max_words_per_chapter}.",
        evidence=(chapter.content or "")[:120],
        repair_instruction="Reach the required range through scene depth or compression, preserving chapter boundaries and avoiding padding.",
    )]


def _target_band(run):
    target = max(run.min_words_per_chapter, min(run.max_words_per_chapter, round(run.target_word_count / run.requested_chapters)))
    return target, max(run.min_words_per_chapter, math.ceil(target * 0.95)), min(run.max_words_per_chapter, math.floor(target * 1.05))


def _repair_priority(issue):
    if issue.category in {"continuity", "causality", "character", "premature_payoff", "unresolved_payoff"}:
        return 0
    return 1 if issue.category == "length" else 2


def _repair_kind(payload):
    if payload.get("repair_kind") in {"targeted", "coordinated", "prose", "chapter"}:
        return payload["repair_kind"]
    # Infer the bucket for old checkpoints instead of resetting their budgets.
    issues = payload.get("issues", [])
    return "prose" if issues and all(issue["category"] in {"prose", "repetition"} for issue in issues) else "chapter"


def _repair(session, run, chapter, ledger, issues, settings, client, phase: str, *, escalate_from=None) -> None:
    pipeline = _pipeline()
    pipeline._ensure_not_canceled(session, run)
    interrupted = {item["started_event_id"] for item in _events(run, "autonomous_repair_interrupted")}
    all_attempts = [event.payload for event in run.events
                if event.event_type == "autonomous_repair_started" and event.id not in interrupted
                and event.payload.get("chapter_number") == chapter.chapter_number and event.payload.get("phase") == phase]
    priority = min(_repair_priority(issue) for issue in issues)
    all_issues = issues
    deferred = [issue for issue in issues if _repair_priority(issue) != priority]
    issues = [issue for issue in issues if _repair_priority(issue) == priority]
    before = chapter.content or ""
    from .local_prose_repair import repair_plan, repair_passages
    plan = repair_plan(before, issues)
    # Related causal and repetition diagnoses must be resolved in one candidate,
    # not starved behind alternating structural edits.
    combined = [issue for issue in all_issues if issue.category != "length"]
    combined_plan = repair_plan(before, combined, max_passages=16)
    coordinated = combined_plan is not None and (len(combined_plan) > 1 or
        any(_repair_kind(attempt) == "coordinated" for attempt in all_attempts))
    if coordinated:
        issues = combined
        plan = combined_plan
        deferred = [issue for issue in all_issues if issue.category == "length"]
    repair_kind = "prose" if priority == 2 else "chapter"
    if priority == 0 and plan is not None:
        repair_kind = "targeted"
    if coordinated:
        repair_kind = "coordinated"
    if any(issue.repair_scope == "chapter" for issue in all_issues):
        # An evidence paragraph can anchor an omitted section or climax without
        # containing the material needed to restore it. Respect the editor's
        # explicit structural scope instead of repeatedly patching that anchor.
        issues, deferred, plan, coordinated = all_issues, [], None, False
        repair_kind = "chapter"
    attempts = [attempt for attempt in all_attempts if _repair_kind(attempt) == repair_kind]
    limit = (settings.autonomous_targeted_repair_attempts if repair_kind in {"targeted", "coordinated"} else
             settings.autonomous_prose_repair_attempts if repair_kind == "prose" else
             settings.autonomous_chapter_repair_attempts if phase == "draft" else settings.autonomous_manuscript_repair_rounds)
    if escalate_from or (len(attempts) >= limit and repair_kind != "chapter"):
        escalate_from = escalate_from or repair_kind
        repair_kind, plan, coordinated = "chapter", None, False
        issues, deferred = all_issues, []
        attempts = [attempt for attempt in all_attempts if _repair_kind(attempt) == "chapter"]
        limit = settings.autonomous_chapter_repair_attempts if phase == "draft" else settings.autonomous_manuscript_repair_rounds
    if len(attempts) >= limit:
        if escalate_from:
            raise AutonomousQualityError(f"Chapter {chapter.chapter_number} exhausted its {phase} {escalate_from} repair budget or could not produce usable edits, and its chapter rewrite budget is exhausted. Unresolved checks prevent completion.")
        raise AutonomousQualityError(f"Chapter {chapter.chapter_number} exhausted its {phase} {repair_kind} repair budget. Unresolved checks prevent completion.")
    if escalate_from:
        _record(session, run, "autonomous_repair_escalated", {
            "message": f"Chapter {chapter.chapter_number}: switching from {escalate_from} repairs to a complete chapter rewrite.",
            "chapter_number": chapter.chapter_number, "phase": phase,
            "from_strategy": escalate_from, "to_strategy": "chapter", "attempt": len(attempts) + 1,
        })
    # A structural rewrite can invalidate line edits and alter length. Recheck the
    # resulting text before spending a later attempt on those lower-level issues.
    backup = settings.artifacts_dir / run.id / "editorial-history" / f"chapter-{chapter.chapter_number}-{_hash(before)[:16]}.md"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(before, encoding="utf-8")
    _record(session, run, "autonomous_repair_started", {
        "message": f"Automatically repairing chapter {chapter.chapter_number}.",
        "chapter_number": chapter.chapter_number, "phase": phase, "attempt": len(attempts) + 1,
        "repair_kind": repair_kind,
        "escalated_from": escalate_from,
        "before_hash": _hash(before), "backup": str(backup.relative_to(settings.artifacts_dir)),
        "issues": [issue.model_dump() for issue in issues],
        "deferred_issues": [issue.model_dump() for issue in deferred],
    })
    provider, model = pipeline._resolve_stage_route(client, run, "autonomous_revision")
    if (coordinated or escalate_from) and not (run.task_routing or {}).get("autonomous_revision"):
        # Use the drafting route as a default for demanding repairs, but honor
        # an explicitly selected editor. It may be stronger than the drafter.
        provider, model = pipeline._resolve_stage_route(client, run, "chapter_draft")
    context = _context(run, chapter, ledger)
    length_instruction = ""
    if any(issue.category == "length" for issue in issues):
        target, minimum, maximum = _target_band(run)
        count = len(before.split())
        context["chapter_word_range"] = [minimum, maximum]
        length_instruction = (
            f"\nLENGTH REPAIR IS REQUIRED: the current text is {count} words. Return {minimum}-{maximum} words, "
            f"aiming for {target}. This overrides the original looser chapter range. "
            "Compress redundant exposition, repeated images, and dialogue if over length; deepen existing "
            "scenes if under length. Preserve the evidence, causal transitions, resolution, and aftermath. "
            "Do not add new events or commentary about word counts. Keeping every existing sentence is not required."
        )
    messages = [
        {"role": "system", "content": "You are a novelist revising a chapter from an independent editorial diagnosis. Return the complete revised chapter as prose only. No heading, explanation, or synopsis. Fix every supplied issue. Preserve author facts, established past events, and the chapter's assigned stopping state. Never consume a future chapter's payoff. In the final chapter, the author's ending promise must be fulfilled in actual scenes even if the outline omitted it. Keep correct passages and the author's voice; do not replace the entire book with this chapter."},
        {"role": "user", "content": json.dumps({"context": context,
                                                   "repairs": [issue.model_dump() for issue in issues]}, ensure_ascii=False)},
    ]
    brief = run_book_brief(run)
    if uses_book_contract(brief):
        messages[0]["content"] = (
            "You are a book writer and editor repairing the complete chapter in its requested form. "
            "Return the entire revised chapter, without a duplicate chapter title, explanation, or synopsis. "
            "Fix every supplied issue and restore missing assigned sections or outcomes. Preserve correct unique "
            "information, supported facts, speaker attribution, and unaffected passages. Do not replace one "
            "record example, scene, or stanza with a copy of another. Respect the chapter's assigned ending state; "
            "do not consume future chapters' work. Complete the author promise in the final chapter.\n"
            + contract_text(brief)
        )
        length_instruction = length_instruction.replace("deepen existing scenes", "add useful detail in the requested book form")
    messages[0]["content"] += length_instruction
    run.current_step = "autonomous_revision"
    run.current_chapter = chapter.chapter_number
    session.commit()
    def generate_repair(messages, passage=1, passage_count=1):
        pipeline._ensure_not_canceled(session, run)
        _check_context(messages, client, provider)
        return pipeline.sanitize_chapter_content(pipeline._supervised_provider_chat(
            session, run, client, provider, model, messages, stage="autonomous_revision",
            chapter_number=chapter.chapter_number, metadata={"phase": phase, "repair_attempt": len(attempts) + 1,
                "repair_mode": "coordinated" if coordinated else "passage" if plan is not None else "chapter",
                "passage": passage, "passage_count": passage_count},
        ))
    def retry_as_chapter(reason):
        _record(session, run, "autonomous_repair_rejected", {
            "message": "Local repair produced no usable candidate; escalating automatically without changing saved prose.",
            "chapter_number": chapter.chapter_number, "phase": phase,
            "repair_kind": repair_kind, "reason": str(reason), "before_hash": _hash(before),
        })
        _repair(session, run, chapter, ledger, all_issues, settings, client, phase, escalate_from=repair_kind)

    try:
        if coordinated:
            from .coordinated_repair import repair
            candidate = repair(before, plan, context, generate_repair)
        else:
            candidate = repair_passages(before, plan, generate_repair, context=context) if plan is not None else generate_repair(messages)
    except ValueError as exc:
        if plan is None:
            raise
        retry_as_chapter(exc)
        return
    if length_instruction and len(candidate.split()) > maximum:
        from .prose_budget import compress_prose
        def compress(messages, passage, passage_count, attempt):
            pipeline._ensure_not_canceled(session, run)
            _check_context(messages, client, provider)
            return pipeline.sanitize_chapter_content(pipeline._supervised_provider_chat(
                session, run, client, provider, model, messages, stage="autonomous_revision",
                chapter_number=chapter.chapter_number,
                metadata={"phase": phase, "repair_attempt": len(attempts) + 1,
                          "compression_passage": passage, "compression_passage_count": passage_count,
                          "compression_attempt": attempt},
            ))
        candidate = compress_prose(candidate, target, compress)
    if not candidate.strip() or _text(candidate) == _text(before):
        if repair_kind != "chapter":
            retry_as_chapter("No usable change was made.")
            return
        raise AutonomousQualityError(f"Chapter {chapter.chapter_number} repair produced no usable change.")
    if any(event.get("before_hash") == _hash(candidate) for event in all_attempts):
        if repair_kind != "chapter":
            retry_as_chapter("The local edit returned to an earlier rejected version.")
            return
        raise AutonomousQualityError(f"Chapter {chapter.chapter_number} repair returned to an earlier rejected version. Saved prose is preserved; change the repair model before resuming.")
    chapter.content = candidate
    chapter.word_count = len(candidate.split())
    chapter.summary = None
    chapter.continuity_update = None
    chapter.qa_notes = None
    _record(session, run, "autonomous_repair_completed", {
        "message": f"Saved chapter {chapter.chapter_number} repair for re-review.",
        "chapter_number": chapter.chapter_number, "phase": phase, "after_hash": _hash(candidate),
    })


def ensure_chapter(session, run, chapter, ledger, settings, client) -> None:
    """Never carry a known bad chapter into subsequent chapter generation."""
    if not enabled(run):
        return
    if not draft_checkpoint(run, chapter):
        _record(session, run, "autonomous_draft_checkpoint", {
            "message": f"Chapter {chapter.chapter_number} entered automatic editing; resume will preserve its revised prose.",
            "chapter_number": chapter.chapter_number,
        })
    while True:
        review = _review(session, run, [chapter], _context(run, chapter, ledger), client, "chapter")
        issues = [*review.issues, *_length_issues(run, chapter)]
        source_review = _source_review(session, run, chapter, client)
        if source_review:
            issues.extend(source_review.issues)
        if review.passed and not issues:
            return
        _repair(session, run, chapter, ledger, issues, settings, client, "draft")


def draft_checkpoint(run, chapter):
    if not enabled(run) or not chapter.content:
        return False
    return any(event.event_type in {"autonomous_draft_checkpoint", "autonomous_repair_started"}
               and event.payload.get("chapter_number") == chapter.chapter_number
               and (event.event_type == "autonomous_draft_checkpoint" or event.payload.get("phase") == "draft")
               for event in run.events)


def _source_review(session, run, chapter, client):
    brief = run_book_brief(run)
    if brief.get("content_kind", "fiction") != "nonfiction":
        return None
    if not brief.get("source_material", "").strip():
        raise AutonomousQualityError("Nonfiction source audit requires the saved source content.")
    return _review(session, run, [chapter], {
        "chapter_number": chapter.chapter_number, "actual_prose": chapter.content,
        "source_material": brief["source_material"],
    }, client, "source_accuracy")


def _refresh_metadata(session, run, chapters, client):
    """Refresh actual prose in order; a changed predecessor invalidates the entire affected suffix."""
    pipeline = _pipeline()
    ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
    ledger_before = {}
    for chapter in chapters:
        pipeline._ensure_not_canceled(session, run)
        run.current_step = "autonomous_reconciliation"
        run.current_chapter = chapter.chapter_number
        session.commit()
        ledger_before[chapter.chapter_number] = ledger
        fingerprint = _hash({"prose": chapter.content, "prior_state": ledger.model_dump()})
        metadata_hash = _hash([chapter.summary, chapter.continuity_update])
        cached = any(item.get("fingerprint") == fingerprint and item.get("chapter_number") == chapter.chapter_number
                     and item.get("metadata_hash") == metadata_hash
                     for item in _events(run, "autonomous_metadata_refreshed"))
        if not cached or not chapter.summary or not chapter.continuity_update:
            provider, model = pipeline._resolve_stage_route(client, run, "chapter_summary")
            chapter.summary = pipeline._supervised_provider_chat(
                session, run, client, provider, model,
                pipeline.build_summary_messages(chapter, pipeline._outline_entry(run, chapter.chapter_number),
                    project=run.project, story_bible=run.story_bible),
                stage="chapter_summary", chapter_number=chapter.chapter_number,
                metadata={"phase": "autonomous_reconciliation"},
            ).strip()
            if not chapter.summary:
                raise AutonomousQualityError("Automatic reconciliation returned an empty summary.")
            provider, model = pipeline._resolve_stage_route(client, run, "continuity_update")

            def parse_snapshot(raw):
                update = parse_continuity_update(raw)
                if not set(LIVE_SNAPSHOT_FIELDS) <= update.model_fields_set:
                    raise ValueError("Automatic reconciliation requires explicit values for all live continuity fields.")
                return update

            update = pipeline._generate_structured_output(
                session, run, client, provider, model,
                lambda: pipeline.build_continuity_update_messages(run.project, chapter, ledger, pipeline._story_bible_from_run(run)),
                parse_snapshot, "automatic continuity reconciliation", "continuity_update", chapter.chapter_number,
            )
            chapter.continuity_update = update.model_dump(exclude_unset=True)
            _record(session, run, "autonomous_metadata_refreshed", {
                "message": f"Refreshed chapter {chapter.chapter_number} summary and continuity from current prose.",
                "chapter_number": chapter.chapter_number, "fingerprint": fingerprint,
                "metadata_hash": _hash([chapter.summary, chapter.continuity_update]),
            })
        else:
            update = parse_continuity_update(json.dumps(chapter.continuity_update))
        ledger = apply_continuity_lifecycle(pipeline._ledger_from_update(ledger, update), update)
        ledger = pipeline._ledger_with_chapter_mode(ledger, chapter.chapter_number, pipeline._outline_entry(run, chapter.chapter_number).chapter_mode)
    run.continuity_ledger = ledger.model_dump()
    session.commit()
    return ledger_before


def _book_context(run, chapters, qa_report) -> dict:
    chapter_map = []
    for chapter in chapters:
        row = {"chapter_number": chapter.chapter_number}
        if chapter is chapters[-1]:
            row["actual_prose_supplied_in_final_chapter_field"] = True
        else:
            row["summary"] = (chapter.summary or "")[:320]
            prose = chapter.content or ""
            if len(prose) <= 480:
                row["actual_prose"] = prose
            else:
                row["opening_prose"] = prose[:160]
                row["ending_prose"] = prose[-320:]
        chapter_map.append(row)
    return {
        "author_brief": run_book_brief(run),
        "ending_promise": (run.story_bible or {}).get("ending_promise", ""),
        "ending_debt_to_verify_against_actual_prose": compile_ending_debt_audit(run, chapters).payload,
        "chapter_map": chapter_map,
        "final_chapter_actual_prose": chapters[-1].content,
        "legacy_qa_concerns_to_verify_against_evidence": {
            key: [str(item)[:350] for item in qa_report.model_dump().get(key, [])[:16]]
            for key in ("warnings", "continuity_risks", "ending_coherence_notes", "repetition_risks")
        },
    }


def _adjacent_context(run, chapters, ledger) -> dict:
    """Compare complete literal prose; a summary cannot substitute for an earlier fact."""
    return {
        "author_brief": run_book_brief(run),
        "world_rules": (run.story_bible or {}).get("world_rules", []),
        "actual_chapters": [{"chapter_number": chapter.chapter_number,
                             "actual_prose": _numbered_prose(chapter.content or "", chapter.chapter_number)}
                            for chapter in chapters],
    }


def save_report(session, run, settings, status: str, **extra) -> None:
    directory = settings.artifacts_dir / run.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "autonomous-quality-report.json"
    report = {"status": status, "run_id": run.id,
              "quality_profile": run.quality_profile,
              "total_words": sum(len((chapter.content or "").split()) for chapter in run.chapters),
              "repair_limits": {"draft_attempts_per_chapter": settings.autonomous_chapter_repair_attempts,
                                "targeted_attempts_per_chapter_per_phase": settings.autonomous_targeted_repair_attempts,
                                "prose_attempts_per_chapter_per_phase": settings.autonomous_prose_repair_attempts,
                                "final_attempts_per_chapter": settings.autonomous_manuscript_repair_rounds},
              "manuscript_hash": _hash([(chapter.chapter_number, chapter.content) for chapter in run.chapters]),
              "reviews": _events(run, "autonomous_review_completed"),
              "rejected_reviews": _events(run, "autonomous_review_rejected"),
              "interrupted_repairs": _events(run, "autonomous_repair_interrupted"),
              "repairs": _events(run, "autonomous_repair_started"), **extra}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not any(artifact.kind == "autonomous-quality-report" for artifact in run.artifacts):
        run.artifacts.append(Artifact(kind="autonomous-quality-report", filename=path.name,
                                     relative_path=str(path.relative_to(settings.artifacts_dir)), content_type="application/json"))
    session.commit()


def finish_manuscript(session, run, chapters, settings, client, qa_report):
    """Only return when every final chapter and the complete book pass automated checks."""
    if not enabled(run):
        return qa_report
    try:
        for round_number in range(settings.autonomous_manuscript_repair_rounds + 1):
            before = _refresh_metadata(session, run, chapters, client)
            issues = []
            for chapter in chapters:
                review = _review(session, run, [chapter], _context(run, chapter, before[chapter.chapter_number]), client, "final_chapter")
                issues.extend(review.issues)
                issues.extend(_length_issues(run, chapter))
                source_review = _source_review(session, run, chapter, client)
                if source_review:
                    issues.extend(source_review.issues)
            for left, right in zip(chapters, chapters[1:]):
                pair = [left, right]
                review = _review(session, run, pair, _adjacent_context(run, pair, before[left.chapter_number]), client, "adjacent_chapters")
                issues.extend(review.issues)
            book_review = _review(session, run, chapters, _book_context(run, chapters, qa_report), client, "whole_book")
            issues.extend(book_review.issues)
            total = sum(len((chapter.content or "").split()) for chapter in chapters)
            if not 0.9 * run.target_word_count <= total <= 1.1 * run.target_word_count:
                target, minimum, maximum = _target_band(run)
                overlong = total > run.target_word_count
                candidates = [chapter for chapter in chapters if
                    (len(chapter.content.split()) > maximum if overlong else len(chapter.content.split()) < minimum)]
                if not candidates:
                    raise AutonomousQualityError("The book word target cannot be reached within the configured chapter ranges.")
                for chapter in candidates:
                    issues.append(EditorialIssue(chapter_number=chapter.chapter_number, category="length",
                        problem=f"Book is {total} words against target {run.target_word_count}.", evidence=chapter.content[:120],
                        repair_instruction=f"Aim for {target} words in this chapter so the whole book meets its target, without consuming later events or padding."))
            if not issues and book_review.passed:
                run.current_chapter = None
                _record(session, run, "autonomous_quality_passed", {
                    "message": "Every chapter and the whole manuscript passed automatic editorial checks.",
                    "total_words": total, "round": round_number,
                    "manuscript_hash": _hash([(chapter.chapter_number, chapter.content) for chapter in chapters]),
                })
                save_report(session, run, settings, "passed", total_words=total)
                from ..schemas import ManuscriptQaReport
                return ManuscriptQaReport(
                    overall_verdict="Automatic editorial checks passed for the final manuscript.",
                    strengths=["Every final chapter passed an evidence-based editorial review.",
                               "Every adjacent chapter pair passed a review of its complete actual prose.",
                               "Whole-book chronology, canon, prose, and ending checks passed.",
                               "Chapter lengths and total manuscript word count passed deterministic checks."],
                    publication_readiness_label="automated checks passed",
                    publication_readiness_summary="See the automatic quality report for the exact reviewed manuscript and repair history.",
                )
            if round_number >= settings.autonomous_manuscript_repair_rounds:
                raise AutonomousQualityError("Manuscript still has unresolved issues after the bounded automatic editorial rounds.")
            by_chapter = {}
            for issue in issues:
                by_chapter.setdefault(issue.chapter_number, []).append(issue)
            for chapter in chapters:
                if chapter.chapter_number in by_chapter:
                    _repair(session, run, chapter, before[chapter.chapter_number], by_chapter[chapter.chapter_number], settings, client, "final")
            # All audit caches depend on the actual prose/context, so edited chapters and affected
            # neighbors are re-reviewed. Metadata will be reconstructed before that next sweep.
        raise AutonomousQualityError("Automatic editorial loop ended without acceptance.")
    except Exception as exc:
        save_report(session, run, settings, "not_passed", error=str(exc))
        raise
