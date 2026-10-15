"""Bounded, checkpointed editorial repair with a fail-closed manuscript acceptance gate."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from ..models import Artifact, ChapterStatus
from ..repositories import record_event
from .autonomous_contracts import EditorialIssue, EditorialReview, SourceParagraphReference
from .context_memory import compile_memory_packet
from .continuity_lifecycle import LIVE_SNAPSHOT_FIELDS, apply_continuity_lifecycle
from .ending_debt import compile_ending_debt_audit
from .prompts import extract_json_payload, parse_continuity_update
from .book_specs import contract_text, uses_book_contract, run_book_brief, review_instruction, source_review_instruction


class AutonomousQualityError(RuntimeError):
    """The manuscript cannot be certified by the configured automatic editorial checks."""


PAIR_SCOPES = {"adjacent_chapters", "cross_chapter"}
ACCEPTANCE_CONTRACT = 2

OUTPUT_ARTIFACT_PATTERN = re.compile(
    r"\b(?:the chapter (?:ends?|ended|concludes?|concluded)(?:\s+(?:on|with|by))?|"
    r"this (?:scene|chapter) (?:shows|reveals|establishes|foreshadows|sets up)|"
    r"as an ai(?: language model)?|insert (?:scene|chapter|dialogue) here)\b", re.IGNORECASE,
)
OUTPUT_ARTIFACT_RULES = (
    "Audit only unintended production notes, chapter synopsis, placeholders, or assistant commentary embedded in finished prose. "
    "Suspected phrases are navigation hints, never proof of a defect. Read the complete chapter and author contract. "
    "Intentional metafiction, narrator address, quoted dialogue, and nonfiction discussion of chapters are valid when appropriate "
    "to the requested form. Do not ban a word, tense, narrative distance, or writing style. A sentence describing how this "
    "chapter ends can be an accidental synopsis instead of actual scene narration; verify its function in context. "
    "Only confirmed unintended output artifacts fail prose_clean. Set plot_coherent, canon_consistent and ending_complete true. "
    "Report prose issues only, with exact evidence_paragraphs and evidence='Source paragraphs', targeted repairs preserving "
    "the passage's intended events, and no optional suggestions. A matching phrase alone must never authorize deletion."
)


VIEWPOINT_RULES = (
    "Distinguish established shared facts from attributed speech, beliefs, guesses, lies, and predictions. "
    "A character can be mistaken, deceptive, or change their mind after a discovery; dialogue is not automatically "
    "an objective statement of world canon. A later revelation can correct an earlier belief without contradicting "
    "the book. Require incompatible facts established as true about the same event, not merely disagreement "
    "between a character's claim and the narrator or another character. Explain the attribution and framing of both claims. "
    "Scenes may skip travel. An unmentioned return journey alone is not a physical contradiction: require explicit "
    "incompatible positions at the same time or an established constraint that prevents travel. A person's or group's "
    "place of origin does not establish their current location. Objects can have multiple names, descriptions, and functions; "
    "different names alone do not establish incompatible facts. Require conflicting attributes or actions, not an invented "
    "rule that the object can have only one function. "
    "Preserve explicit author facts when assessing possible corrections."
)


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


def _repeated_passages(text):
    groups = {}
    for index, paragraph in enumerate(_paragraphs(text), 1):
        # Ignore headings and short repeated lines. Long exact copies are
        # evidence to inspect, not an automatic defect: refrains and reference
        # instructions may deliberately repeat in the requested book form.
        normalized = re.sub(r'^#{1,6}\s+.*\n', '', paragraph).strip()
        normalized = _text(normalized)
        if len(normalized.split()) >= 30:
            groups.setdefault(normalized, []).append(index)
    return [{"source_paragraphs": numbers, "actual_excerpt": passage}
            for passage, numbers in groups.items() if len(numbers) > 1]


def _book_repeated_passages(chapters):
    groups = {}
    for chapter in chapters:
        for index, paragraph in enumerate(_paragraphs(chapter.content or ""), 1):
            normalized = _text(re.sub(r'^#{1,6}\s+.*\n', '', paragraph).strip())
            if len(normalized.split()) >= 30:
                groups.setdefault(normalized, []).append({"chapter_number": chapter.chapter_number, "paragraph": index})
    # Supporting hints may be bounded; complete source chapters remain intact
    # in their own audits. Prioritize the largest cross-chapter copies.
    matches = [(passage, locations) for passage, locations in groups.items()
               if len({place["chapter_number"] for place in locations}) > 1]
    matches.sort(key=lambda row: len(row[0].split()) * (len(row[1]) - 1), reverse=True)
    return [{"source_locations": locations, "actual_excerpt": passage[:500]}
            for passage, locations in matches[:16]]


def _coverage_marker(chapter_numbers, scope=None, *, paragraph_count=None, paragraph_counts=None):
    from .structured_schema_runtime import make_schema_marker, response_schema_for_stage
    schema = response_schema_for_stage("autonomous_review")
    schema["properties"]["reviewed_chapters"]["const"] = sorted(chapter_numbers)
    schema["$defs"]["EditorialIssue"]["properties"]["chapter_number"]["enum"] = sorted(chapter_numbers)
    definition = schema["$defs"]["EditorialIssue"]
    definition["required"] = list(dict.fromkeys([*definition["required"], "repair_scope"]))
    if scope in {"chapter", "final_chapter", "developmental_chapter", "canon_identity", *PAIR_SCOPES, "source_accuracy", "output_artifacts"}:
        # Complete prose in these scopes is always numbered. References also
        # anchor omission diagnoses, which cannot quote an absent sentence.
        issue = schema["$defs"]["EditorialIssue"]
        issue["required"] = list(dict.fromkeys([*issue["required"], "evidence_paragraphs"]))
        issue["properties"]["evidence"]["const"] = "Source paragraphs"
        if scope not in PAIR_SCOPES:
            issue["properties"]["evidence_paragraphs"]["minItems"] = 1
        if paragraph_count is not None and scope not in PAIR_SCOPES:
            issue["properties"]["evidence_paragraphs"]["items"]["enum"] = list(range(1, paragraph_count + 1))
    definition["properties"]["source_references"]["const"] = []
    if scope in PAIR_SCOPES:
        definition["required"].append("source_references")
        definition["properties"]["evidence_paragraphs"]["const"] = []
        references = definition["properties"]["source_references"]
        references.pop("const")
        references["minItems"] = 1
        # The decoder gets the actual paragraph range for EACH chapter. A flat
        # integer array cannot distinguish chapter 16 from paragraph 16.
        if paragraph_counts is not None:
            references["items"] = {"anyOf": [{
                "type": "object", "additionalProperties": False,
                "required": ["chapter_number", "paragraph_number"],
                "properties": {"chapter_number": {"type": "integer", "const": number},
                               "paragraph_number": {"type": "integer", "enum": list(range(1, paragraph_counts[number] + 1))}},
            } for number in sorted(chapter_numbers)]}
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
    if scope == "canon_identity":
        for check in ("plot_coherent", "prose_clean", "ending_complete"):
            schema["properties"][check]["const"] = True
        definition["properties"]["category"]["enum"] = ["character", "continuity"]
        schema["properties"]["optional_suggestions"]["maxItems"] = 0
    if scope == "developmental_chapter":
        schema["properties"]["canon_consistent"]["const"] = True
        definition["properties"]["category"]["enum"] = ["character", "causality", "premature_payoff", "unresolved_payoff", "repetition", "prose"]
    if scope == "output_artifacts":
        for check in ("plot_coherent", "canon_consistent", "ending_complete"):
            schema["properties"][check]["const"] = True
        definition["properties"]["category"]["const"] = "prose"
        schema["properties"]["optional_suggestions"]["maxItems"] = 0
    return make_schema_marker("autonomous_review", schema=schema)


PAIR_EVIDENCE_RULES = (
    "For every issue choose chapter_number as the ONE chapter to repair. Set evidence='Source paragraphs' and "
    "evidence_paragraphs=[]. Cite the conflicting claims using source_references objects, each with separate "
    "chapter_number and paragraph_number taken from a [Chapter N, paragraph P] source label. "
    "Never flatten chapter and paragraph numbers into one list. Citation order does not choose repair order. "
    "Conflicts can be between chapters or within one supplied chapter; do not invent a citation in the other chapter. "
    "References to the other chapter are supporting context; repair_instruction must change only the selected chapter. "
    "Use assigned_chapter_progression only to choose a repair that preserves the intended event order, never as evidence "
    "that a planned event actually happened. If an earlier chapter prematurely claims completion of an event assigned to "
    "the later chapter, correct that earlier claim and preserve the later on-page action. Never reorder or renumber chapters. "
    "Explain both conflicting statements and their source chapters in problem."
)


BOOKWIDE_CONTINUITY_RULES = (
    "Check stable family relationships, ownership, legal rights, object destruction, knowledge, and system shutdowns. "
    "After an irreversible action, later prose must respect the changed state. A second surrender of already-transferred "
    "rights or destruction of an already-destroyed object needs an established distinct document, retained right, replacement, "
    "or on-page intervening cause; do not invent one merely because multiple copies or aliases could exist. A receipt is not "
    "a new ownership right. The final chapter can deliver aftermath without repeating a sacrifice already enacted. "
    "Distinguish changed circumstances and deliberate changes of mind from unmotivated resets to ignorance of established facts. "
    "For distant chapters, intervening summaries are navigation hints, not complete proof: do not infer that no intervening "
    "event occurred solely because a summary omits it. Independent collection pieces and hypothetical examples may reuse "
    "names or objects; apply these constraints only where the author's book contract establishes shared continuity. "
    "Keep actual inconsistent family roles blocking; do not invent unestablished parentage to explain them."
    " Work already begun can remain unfinished: earlier partial dismantling is compatible with a later pending final shutdown. "
    "Do not turn progress versus remaining work into a contradiction."
)


DEVELOPMENTAL_RULES = (
    "This is a separate developmental audit of a COMPLETE current chapter in the context of the complete book plan "
    "and source-derived chapter openings/endings. Check premature moral or plot payoffs, unexplained character resets, "
    "scenes that merely reenact a completed resolution, and materially repetitive exposition or thematic codas. "
    "A recurring phrase or image count is a diagnostic clue, never an automatic failure; motifs, refrains, reference "
    "definitions, quiet aftermath, poetry, and independent collection pieces can repeat intentionally. "
    "For continuous narratives, require a meaningful new scene consequence, relationship turn, insight, or complication "
    "rather than another declaration of an already-established decision. A change of mind can be valid if the prose "
    "provides its cause. Prefer concrete targeted repairs, retaining effective imagery and dialogue. "
    "Compare the target's actual ending with its assigned character_turn and ending_state: assigned uncertainty is not "
    "delivered by an already-complete moral conversion followed by chapters that rediscover the same facts. Distinguish "
    "a mere intention from narration that declares a transformation complete. Don't request a later chapter's events. "
    "Do not impose a uniform voice, ban lyrical writing, add arbitrary backstory, or pad the chapter. "
    "Quote or cite actual passages from the target chapter supporting every required defect. Supporting excerpts are "
    "explicitly partial: do not claim to have read their full chapters. Grammar alone cannot establish developmental quality. "
    "Set the relevant checks false for demonstrated material defects; reserve optional_suggestions for taste."
) + "\n" + VIEWPOINT_RULES


IDENTITY_RULES = (
    "You are a stable-identity auditor, not a plot or prose critic. Compare this complete chapter's speaker attribution, "
    "names and family relationships against supplied stable author canon and against its own explicit narration. "
    "A chapter calling the same two people sisters and then a mother/daughter pair is a required identity correction "
    "unless the actual text establishes a different referent or relationship. A shared parent cannot silently become "
    "a different generation. Check the entire source for an explanation, not an imagined one. "
    "Do not audit object transfers, travel, technical progress, style or ending in this scope. "
    "Set plot_coherent, prose_clean and ending_complete true. canon_consistent is false for demonstrated identity "
    "defects. Only character or continuity issues are allowed, with exact source paragraph evidence and a local repair. "
    "optional_suggestions must be empty. Plans/canon are author constraints; cite the conflicting actual prose, not a fabricated quotation."
) + "\n" + VIEWPOINT_RULES


def _source_paragraph_counts(context):
    return {row["chapter_number"]: len(_paragraphs(row["actual_prose"]))
            for row in context.get("actual_chapters", [])}


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
        issue._reviewed_source_hashes = {chapter.chapter_number: _hash(chapter.content or "") for chapter in chapters}
        if issue.source_references:
            references = {(ref.chapter_number, ref.paragraph_number) for ref in issue.source_references}
            for number, index in references:
                if number not in paragraphs or not 1 <= index <= len(paragraphs[number]):
                    raise ValueError(f"Source reference Chapter {number}, paragraph {index} is outside the supplied actual prose.")
            target = sorted(index for number, index in references if number == issue.chapter_number)
            if not target:
                raise ValueError("Source references must include evidence in the chapter selected for repair.")
            if issue.evidence_paragraphs and issue.evidence_paragraphs != target:
                raise ValueError("Target paragraph evidence disagrees with the explicit source references.")
            # Canonicalize citation order losslessly, then derive local edit
            # anchors ONLY from the selected chapter. Earlier evidence must
            # never become a paragraph edit in a different chapter.
            issue.source_references = [SourceParagraphReference(chapter_number=number, paragraph_number=index)
                                       for number, index in sorted(references)]
            issue._source_evidence = [dict(chapter_number=number, paragraph_number=index,
                                          actual_prose=paragraphs[number][index - 1])
                                      for number, index in sorted(references)]
            issue.evidence_paragraphs = target
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
        order_commands = re.finditer(r"\breorder\s+(?:(?:the|these|both|two|adjacent)\s+){0,2}chapters\b"
                                     r"|\b(?:renumber|swap)\s+(?:(?:the|these|both|two|adjacent)\s+){0,2}chapters?\b"
                                     r"|\b(?:reorder|move)\s+chapter\s+\d+\s+(?:to\s+)?(?:precede|before|after)\b",
                                     issue.repair_instruction, re.IGNORECASE)
        if any(not re.search(r"\b(?:not|never|without)\s*$", issue.repair_instruction[:command.start()], re.IGNORECASE)
               for command in order_commands):
            raise ValueError("A chapter repair cannot change chapter order. Choose a concrete correction within the selected chapter that preserves assigned_chapter_progression.")
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
        "repeated_passages_to_verify": _repeated_passages(chapter.content or ""),
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
    allegations = [{key: issue.model_dump()[key] for key in
        ("chapter_number", "category", "problem", "evidence", "evidence_paragraphs", "source_references")}
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
    elif scope in PAIR_SCOPES:
        instruction += (
            " Only compare factual continuity, character identity, and causes across these complete chapters. "
            "Only continuity, character, and causality issues are allowed. Set prose_clean and ending_complete to true; "
            "separate audits cover those checks. optional_suggestions must be empty. Explain both incompatible claims and their chapters."
        ) + "\n" + VIEWPOINT_RULES + "\n" + PAIR_EVIDENCE_RULES
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
    if scope in PAIR_SCOPES:
        instruction += "\n" + BOOKWIDE_CONTINUITY_RULES
    if scope == "developmental_chapter":
        instruction += "\n" + DEVELOPMENTAL_RULES
    if scope == "canon_identity":
        instruction += "\n" + IDENTITY_RULES
    if scope == "output_artifacts":
        instruction += "\n" + OUTPUT_ARTIFACT_RULES + "\n" + contract_text(context.get("author_brief"))
    messages = [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(
        {"scope": scope, **source, "proposed_review_to_verify": allegations}, ensure_ascii=False)}]
    if controls:
        from .provider_controls import make_ollama_controls_marker
        messages.insert(0, make_ollama_controls_marker(**controls))
    messages.insert(-1, _coverage_marker(review.reviewed_chapters, scope,
        paragraph_count=len(_paragraphs(context["actual_prose"])) if "actual_prose" in context else None,
        paragraph_counts=_source_paragraph_counts(context) if scope in PAIR_SCOPES else None))
    return messages


def _review(session, run, chapters, context: dict, client, scope: str) -> EditorialReview:
    pipeline = _pipeline()
    pipeline._ensure_not_canceled(session, run)
    review_stage = "autonomous_book_review" if scope == "whole_book" and (run.task_routing or {}).get("autonomous_book_review") else "autonomous_review"
    provider, model = pipeline._resolve_stage_route(client, run, review_stage)
    adjudication_stage = "autonomous_adjudication" if (run.task_routing or {}).get("autonomous_adjudication") else "autonomous_revision"
    adjudicator_provider, adjudicator_model = pipeline._resolve_stage_route(client, run, adjudication_stage)
    from .providers import review_model_identity, review_cache_allowed, require_unchanged_review_models
    reviewer_identity = review_model_identity(client, provider, model)
    adjudicator_identity = review_model_identity(client, adjudicator_provider, adjudicator_model)
    controls = {}
    adjudication_controls = {}
    if provider == "ollama" or adjudicator_provider == "ollama":
        from ..settings import get_settings
        config = getattr(client, "settings", None) or get_settings()
    if scope in {*PAIR_SCOPES, "source_accuracy"} and provider == "ollama":
        if getattr(config, "ollama_continuity_think", None) is not None:
            controls["think"] = config.ollama_continuity_think
        if getattr(config, "ollama_continuity_temperature", None) is not None:
            controls["temperature"] = config.ollama_continuity_temperature
    if adjudicator_provider == "ollama":
        if getattr(config, "ollama_adjudication_think", None) is not None:
            adjudication_controls["think"] = config.ollama_adjudication_think
        if getattr(config, "ollama_adjudication_temperature", None) is not None:
            adjudication_controls["temperature"] = config.ollama_adjudication_temperature
    # Recheck pairs under the complete source-reference contract, including
    # internal contradictions surfaced while comparing their event sequence.
    fingerprint_inputs = {"contract": 26 if scope in PAIR_SCOPES else 25 if scope in {"whole_book", "developmental_chapter", "canon_identity"} else 22, "context": context, "provider": provider, "model": model, "scope": scope, "controls": controls,
                         "adjudication_controls": adjudication_controls,
                         "adjudicator": [adjudicator_provider, adjudicator_model]}
    fingerprint_inputs.update(reviewer_identity=reviewer_identity, adjudicator_identity=adjudicator_identity)
    if scope in {"whole_book", "developmental_chapter"}:
        # Compacted excerpts can stay identical after a middle passage changes.
        # Such a change must still invalidate the complete-book verdict.
        fingerprint_inputs["manuscript_hash"] = _hash([(chapter.chapter_number, chapter.content) for chapter in chapters])
    fingerprint = _hash(fingerprint_inputs)
    for saved in reversed(_events(run, "autonomous_review_completed")):
        if saved.get("fingerprint") == fingerprint and review_cache_allowed(reviewer_identity, adjudicator_identity):
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
    if scope in PAIR_SCOPES:
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
            + PAIR_EVIDENCE_RULES + " Return valid JSON matching this contract: "
            + json.dumps(EditorialReview.model_json_schema())
            + " Perform one comparison pass. If no demonstrated contradiction remains, return the clean review immediately. "
            "Do not revisit a settled calculation or invent another requirement."
        ) + "\n" + VIEWPOINT_RULES
    review_context = dict(context)
    if "actual_prose" in context:
        review_context["actual_prose"] = _numbered_prose(context["actual_prose"], context["chapter_number"])
    if "final_chapter_actual_prose" in context:
        review_context["final_chapter_actual_prose"] = _numbered_prose(context["final_chapter_actual_prose"], max(chapter.chapter_number for chapter in chapters))
    if uses_book_contract(context.get("author_brief")):
        if scope in PAIR_SCOPES:
            instruction += "\nThe following book-form contract governs interpretation of the checks and closure requirements:\n" + contract_text(context["author_brief"])
        else:
            instruction = review_instruction(context["author_brief"], scope, context.get("is_final_chapter")) + "\n" + json.dumps(EditorialReview.model_json_schema())
    if scope == "source_accuracy":
        instruction = source_review_instruction() + "\n" + json.dumps(EditorialReview.model_json_schema())
    if scope == "developmental_chapter":
        instruction += "\n" + DEVELOPMENTAL_RULES
    if scope == "canon_identity":
        instruction = IDENTITY_RULES + "\n" + contract_text(context.get("author_brief")) + "\n" + json.dumps(EditorialReview.model_json_schema())
    if scope == "output_artifacts":
        instruction = OUTPUT_ARTIFACT_RULES + "\n" + contract_text(context.get("author_brief")) + "\n" + json.dumps(EditorialReview.model_json_schema())
    if scope in PAIR_SCOPES:
        instruction += "\n" + BOOKWIDE_CONTINUITY_RULES
    messages = [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps({"scope": scope, **review_context}, ensure_ascii=False)}]
    if controls:
        from .provider_controls import make_ollama_controls_marker
        messages.insert(0, make_ollama_controls_marker(**controls))
    messages.insert(-1, _coverage_marker([chapter.chapter_number for chapter in chapters], scope,
        paragraph_count=len(_paragraphs(chapters[0].content or "")) if len(chapters) == 1 else None,
        paragraph_counts={chapter.chapter_number: len(_paragraphs(chapter.content or "")) for chapter in chapters}
            if scope in PAIR_SCOPES else None))
    _check_context(messages, client, provider)
    run.current_step = "autonomous_review"
    run.current_chapter = chapters[0].chapter_number if len(chapters) == 1 else None
    session.commit()
    def parse_review(raw):
        try:
            review = validate_review(raw, chapters)
            if scope in {"chapter", "final_chapter", "developmental_chapter", "canon_identity", *PAIR_SCOPES, "output_artifacts"}:
                validate_chapter_repair_scope(review)
            if scope in PAIR_SCOPES and (not review.prose_clean or not review.ending_complete or
                any(issue.category not in {"continuity", "character", "causality"} for issue in review.issues)):
                raise ValueError("Chapter-pair audits compare factual continuity, character identity, and causes only. Prose and ending checks belong to the separate chapter and whole-book audits.")
            if scope in PAIR_SCOPES and any(not issue.source_references for issue in review.issues):
                raise ValueError("New chapter-pair reviews must use explicit source_references objects with separate chapter and paragraph numbers.")
            if scope == "canon_identity" and (not all((review.plot_coherent, review.prose_clean, review.ending_complete))
                or any(i.category not in {"character", "continuity"} for i in review.issues) or review.optional_suggestions):
                raise ValueError("Identity audits judge stable identity only, with other checks true and no optional suggestions.")
            if scope == "developmental_chapter" and (not review.canon_consistent or any(i.category == "continuity" for i in review.issues)):
                raise ValueError("Developmental audits judge progression and prose; factual continuity belongs to dedicated pair/identity audits.")
            if scope == "source_accuracy" and (not all((review.plot_coherent, review.prose_clean, review.ending_complete))
                or any(issue.category != "factual" for issue in review.issues) or review.optional_suggestions):
                raise ValueError("Source audits judge factual support only; keep other checks true and report only factual issues without optional suggestions.")
            if scope == "output_artifacts" and (not all((review.plot_coherent, review.canon_consistent, review.ending_complete))
                or any(issue.category != "prose" for issue in review.issues) or review.optional_suggestions):
                raise ValueError("Output-artifact audits judge unintended production text only; keep other checks true and report prose issues without optional suggestions.")
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
    if (review.issues and scope in {"chapter", "final_chapter", "developmental_chapter", "canon_identity", "whole_book", *PAIR_SCOPES, "source_accuracy", "output_artifacts"}
            and (scope in PAIR_SCOPES or scope in {"developmental_chapter", "canon_identity", "whole_book", "output_artifacts"} or len(prior_repairs) >= 2)):
        # A separate decision pass checks a repeatedly revised chapter against
        # the actual contract, rather than endlessly following new preferences.
        # Pair allegations are always verified before the first mutation: a
        # mistaken character belief must not rewrite established author canon.
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
            "reviewer_identity": reviewer_identity, "adjudicator_identity": adjudicator_identity,
        })
    require_unchanged_review_models(client, (provider, model, reviewer_identity),
                                   (adjudicator_provider, adjudicator_model, adjudicator_identity))
    _record(session, run, "autonomous_review_completed", {
        "message": f"Automatic {scope} review {'passed' if review.passed else 'requested repairs'}.",
        "scope": scope, "fingerprint": fingerprint, "review": review.model_dump(),
        "reviewer_identity": reviewer_identity, "adjudicator_identity": adjudicator_identity,
        "source_hashes": {str(chapter.chapter_number): _hash(chapter.content or "") for chapter in chapters},
        "context_source_hashes": context.get("context_source_hashes", {}),
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
    current_sources = {item.chapter_number: _hash(item.content or "") for item in run.chapters}
    changed_sources = sorted({number for issue in issues for number, source_hash in issue._reviewed_source_hashes.items()
                              if current_sources.get(number) != source_hash})
    if changed_sources:
        # An earlier edit in this manuscript sweep can invalidate a later
        # diagnosis or supply its missing counterevidence. Rejudge current
        # prose before spending an attempt on a now-stale allegation.
        _record(session, run, "autonomous_repair_deferred", {
            "message": f"Chapter {chapter.chapter_number}: source changed after review; requesting a fresh audit before repair.",
            "chapter_number": chapter.chapter_number, "phase": phase,
            "changed_source_chapters": changed_sources,
        })
        return
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
    cited_sources = {(row["chapter_number"], row["paragraph_number"]): row
                    for issue in issues for row in issue._source_evidence}
    if cited_sources:
        context["verified_source_evidence"] = [cited_sources[key] for key in sorted(cited_sources)]
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
    messages[0]["content"] += (
        "\nPreserve correct established events, not the events identified as erroneous in the diagnoses. "
        "Remove or change a diagnosed contradiction or premature scene when required, and repair its transitions."
    )
    if cited_sources:
        messages[0]["content"] += (
            "\nverified_source_evidence contains exact source passages attached by the application. "
            "Use them to resolve the conflict; passages in other chapters are read-only context. "
            "Change only the selected chapter and preserve its assigned stopping state."
        )
    run.current_step = "autonomous_revision"
    run.current_chapter = chapter.chapter_number
    session.commit()
    def generate_repair(messages, passage=1, passage_count=1):
        whole_chapter = plan is None and not coordinated
        previous_versions = {attempt.get("before_hash") for attempt in all_attempts}
        repeated_structure = whole_chapter and any(issue.repair_scope == "chapter" and issue.category != "length" for issue in issues) and any(
            any(issue.get("repair_scope") == "chapter" and issue.get("category") != "length"
                for issue in attempt.get("issues", [])) for attempt in all_attempts)
        reason = "a structural diagnosis remains after an earlier chapter rewrite" if repeated_structure else ""
        for candidate_attempt in range(0 if repeated_structure else 2 if whole_chapter else 1):
            pipeline._ensure_not_canceled(session, run)
            candidate_messages = list(messages)
            if candidate_attempt:
                candidate_messages.append({"role": "user", "content": (
                    f"The previous proposal was rejected: {reason}. Return the complete revised chapter with an actual "
                    "correction to the diagnosed defect. Work within this chapter, preserve the assigned progression, "
                    "and keep correct unique passages. Do not copy the unchanged chapter or an earlier rejected version."
                )})
            _check_context(candidate_messages, client, provider)
            candidate = pipeline.sanitize_chapter_content(pipeline._supervised_provider_chat(
                session, run, client, provider, model, candidate_messages, stage="autonomous_revision",
                chapter_number=chapter.chapter_number, metadata={"phase": phase, "repair_attempt": len(attempts) + 1,
                    "repair_mode": "coordinated" if coordinated else "passage" if plan is not None else "chapter",
                    "passage": passage, "passage_count": passage_count, "candidate_attempt": candidate_attempt + 1},
            ))
            if not whole_chapter:
                return candidate
            reason = ("no usable change was made" if not candidate.strip() or _text(candidate) == _text(before) else
                      "the proposal returned to an earlier rejected version" if _hash(candidate) in previous_versions else "")
            if not reason:
                return candidate
            _record(session, run, "autonomous_repair_candidate_rejected", {
                "message": "Chapter rewrite made no usable progress; retrying from the original source.",
                "chapter_number": chapter.chapter_number, "phase": phase,
                "candidate_attempt": candidate_attempt + 1, "reason": reason, "before_hash": _hash(before),
            })
        # Repeating the same full-source rewrite can keep eliciting a verbatim
        # copy. Switch representations within this persisted repair attempt:
        # the model proposes explicit source ranges, and untouched prose stays
        # byte-for-byte intact. Fresh audits still decide whether the edit works.
        from .chapter_range_repair import repair as repair_ranges
        from .provider_controls import make_ollama_controls_marker
        _record(session, run, "autonomous_repair_strategy_changed", {
            "message": "Chapter rewrite did not resolve the diagnosis; switching automatically to source paragraph edits.",
            "chapter_number": chapter.chapter_number, "phase": phase,
            "from_strategy": "chapter", "to_strategy": "paragraph_ranges", "reason": reason,
        })
        def generate_ranges(range_messages, range_attempt):
            pipeline._ensure_not_canceled(session, run)
            controls = {}
            if provider == "ollama":
                config = getattr(client, "settings", None) or settings
                if config.ollama_adjudication_think is not None:
                    controls["think"] = config.ollama_adjudication_think
                if config.ollama_adjudication_temperature is not None:
                    controls["temperature"] = config.ollama_adjudication_temperature
            if controls:
                range_messages = [make_ollama_controls_marker(**controls), *range_messages]
            _check_context(range_messages, client, provider)
            return pipeline._supervised_provider_chat(
                session, run, client, provider, model, range_messages, stage="autonomous_revision",
                chapter_number=chapter.chapter_number, metadata={"phase": phase,
                    "repair_attempt": len(attempts) + 1, "repair_mode": "paragraph_ranges",
                    "candidate_attempt": range_attempt, "controls": controls},
            )
        try:
            return repair_ranges(before, issues, context, generate_ranges)
        except ValueError as exc:
            raise AutonomousQualityError(f"Chapter {chapter.chapter_number} repair produced no usable change ({reason}): {exc}") from exc
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
        if not issues:
            # Resolve already-known defects before spending a second review on
            # prose that is about to change. Never accept it without identity QA.
            identity = _review(session, run, [chapter], _identity_context(run, chapter), client, "canon_identity")
            issues.extend(identity.issues)
        artifact_review = _output_artifact_review(session, run, chapter, client)
        if artifact_review:
            issues.extend(artifact_review.issues)
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


def _output_artifact_review(session, run, chapter, client):
    # Hints select an audit, never an automatic rewrite or text deletion.
    hints = list(dict.fromkeys(match.group() for match in OUTPUT_ARTIFACT_PATTERN.finditer(chapter.content or "")))
    if not hints:
        return None
    return _review(session, run, [chapter], {
        "chapter_number": chapter.chapter_number, "actual_prose": chapter.content,
        "author_brief": run_book_brief(run), "suspected_output_artifacts_to_verify": hints[:20],
    }, client, "output_artifacts")


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
        fingerprint = _hash({"contract": 2, "prose": chapter.content, "prior_state": ledger.model_dump()})
        metadata_hash = _hash([chapter.summary, chapter.continuity_update])
        cached = any(item.get("fingerprint") == fingerprint and item.get("chapter_number") == chapter.chapter_number
                     and item.get("metadata_hash") == metadata_hash
                     for item in _events(run, "autonomous_metadata_refreshed"))
        if not cached or not chapter.summary or not chapter.continuity_update:
            provider, model = pipeline._resolve_stage_route(client, run, "chapter_summary")
            refreshed_summary = pipeline._supervised_provider_chat(
                session, run, client, provider, model,
                pipeline.build_summary_messages(chapter, pipeline._outline_entry(run, chapter.chapter_number),
                    project=run.project, story_bible=run.story_bible),
                stage="chapter_summary", chapter_number=chapter.chapter_number,
                metadata={"phase": "autonomous_reconciliation"},
            ).strip()
            if not refreshed_summary:
                raise AutonomousQualityError("Automatic reconciliation returned an empty summary.")
            provider, model = pipeline._resolve_stage_route(client, run, "continuity_update")

            def parse_snapshot(raw):
                update = parse_continuity_update(raw)
                if not set(LIVE_SNAPSHOT_FIELDS) <= update.model_fields_set:
                    raise ValueError("Automatic reconciliation requires explicit values for all live continuity fields.")
                return update

            update = pipeline._generate_structured_output(
                session, run, client, provider, model,
                lambda: pipeline.build_continuity_update_messages(run.project, chapter, ledger, pipeline._story_bible_from_run(run),
                    summary_override=refreshed_summary),
                parse_snapshot, "automatic continuity reconciliation", "continuity_update", chapter.chapter_number,
            )
            chapter.summary = refreshed_summary
            chapter.continuity_update = update.model_dump(exclude_unset=True)
            # Draft completion is separate from the still-pending final quality
            # checks. A failed metadata refresh must not discard that draft.
            chapter.status = ChapterStatus.COMPLETED
            chapter.error_message = None
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
        "premise": run.project.premise,
        "ending_promise": (run.story_bible or {}).get("ending_promise", ""),
        "ending_debt_to_verify_against_actual_prose": compile_ending_debt_audit(run, chapters).payload,
        "chapter_map": chapter_map,
        "repeated_passages_to_verify": _book_repeated_passages(chapters),
        "final_chapter_actual_prose": chapters[-1].content,
        "legacy_qa_concerns_to_verify_against_evidence": {
            key: [str(item)[:350] for item in qa_report.model_dump().get(key, [])[:16]]
            for key in ("warnings", "continuity_risks", "ending_coherence_notes", "repetition_risks")
        },
    }


def _adjacent_context(run, chapters, ledger) -> dict:
    """Compare complete literal prose; a summary cannot substitute for an earlier fact."""
    numbers = {chapter.chapter_number for chapter in chapters}
    return {
        "author_brief": run_book_brief(run),
        "premise": run.project.premise,
        "world_rules": (run.story_bible or {}).get("world_rules", []),
        "stable_character_canon": [{"name": row.get("name"), "relationship_to_protagonist": row.get("relationship_to_protagonist")}
                                   for row in (run.story_bible or {}).get("character_agendas", [])],
        "assigned_chapter_progression": [entry for entry in (run.outline or [])
                                         if entry["chapter_number"] in numbers],
        "actual_chapters": [{"chapter_number": chapter.chapter_number,
                             "actual_prose": _numbered_prose(chapter.content or "", chapter.chapter_number)}
                            for chapter in chapters],
    }


def _cross_chapter_context(run, chapters, ledger):
    context = _adjacent_context(run, chapters, ledger)
    left, right = chapters
    context["source_scope"] = "Two complete source chapters; intervening summaries are partial navigation context."
    summary_limit = min(500, max(40, 8000 // max(1, right.chapter_number - left.chapter_number - 1)))
    context["intervening_chapter_summaries"] = [
        {"chapter_number": c.chapter_number, "summary": (c.summary or "")[:summary_limit]}
        for c in run.chapters if left.chapter_number < c.chapter_number < right.chapter_number]
    context["context_source_hashes"] = {str(c.chapter_number): _hash(c.content or "") for c in run.chapters
        if left.chapter_number <= c.chapter_number <= right.chapter_number}
    return context


def _developmental_context(run, chapter, ledger):
    context = _context(run, chapter, ledger)
    field_limit = min(240, max(25, 9000 // max(1, 3 * len(run.outline or []))))
    excerpt_limit = min(720, max(90, 12000 // max(1, len(run.chapters))))
    context["complete_book_plan"] = [
        {"chapter_number": row["chapter_number"], **{key: str(row.get(key) or "")[:field_limit]
            for key in ("objective", "character_turn", "ending_state")}}
        for row in (run.outline or [])]
    context["source_derived_openings_and_endings"] = [
        {"chapter_number": c.chapter_number, "opening_excerpt": (c.content or "")[:excerpt_limit // 3],
         "ending_excerpt": (c.content or "")[-(2 * excerpt_limit // 3):]} for c in run.chapters if c.chapter_number != chapter.chapter_number]
    context["context_source_hashes"] = {str(c.chapter_number): _hash(c.content or "") for c in run.chapters}
    return context


def _identity_context(run, chapter):
    bible = run.story_bible or {}
    return {"author_brief": run_book_brief(run), "chapter_number": chapter.chapter_number,
        "actual_prose": chapter.content or "", "stable_character_canon": {
            "cast": bible.get("cast", []), "relationships": [{"name": row.get("name"),
                "relationship_to_protagonist": row.get("relationship_to_protagonist")}
                for row in bible.get("character_agendas", [])]}}


def _apply_final_repairs(session, run, chapters, before, issues, settings, client, round_number):
    if round_number >= settings.autonomous_manuscript_repair_rounds:
        raise AutonomousQualityError("Manuscript still has unresolved issues after the bounded automatic editorial rounds.")
    by_chapter = {}
    for issue in issues:
        by_chapter.setdefault(issue.chapter_number, []).append(issue)
    for chapter in chapters:
        group = by_chapter.get(chapter.chapter_number, [])
        if not group:
            continue
        hashes = {c.chapter_number: _hash(c.content or "") for c in chapters}
        if any(hashes.get(number) != digest for issue in group for number, digest in issue._reviewed_source_hashes.items()):
            _record(session, run, "autonomous_reaudit_required", {
                "message": "An earlier repair changed supporting prose; re-audit remaining diagnoses before editing.",
                "chapter_number": chapter.chapter_number})
            return
        _repair(session, run, chapter, before[chapter.chapter_number], group, settings, client, "final")


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
                identity = _review(session, run, [chapter], _identity_context(run, chapter), client, "canon_identity")
                issues.extend(identity.issues)
                developmental = _review(session, run, [chapter], _developmental_context(run, chapter, before[chapter.chapter_number]), client, "developmental_chapter")
                issues.extend(developmental.issues)
                issues.extend(_length_issues(run, chapter))
                artifact_review = _output_artifact_review(session, run, chapter, client)
                if artifact_review:
                    issues.extend(artifact_review.issues)
                source_review = _source_review(session, run, chapter, client)
                if source_review:
                    issues.extend(source_review.issues)
            if issues:
                _apply_final_repairs(session, run, chapters, before, issues, settings, client, round_number)
                continue
            for left, right in zip(chapters, chapters[1:]):
                pair = [left, right]
                review = _review(session, run, pair, _adjacent_context(run, pair, before[left.chapter_number]), client, "adjacent_chapters")
                issues.extend(review.issues)
            # Every non-neighboring pair also gets complete source comparison.
            # Persisted fingerprints reuse unchanged audits after interruptions.
            for left_index, left in enumerate(chapters):
                for right in chapters[left_index + 2:]:
                    pair = [left, right]
                    review = _review(session, run, pair, _cross_chapter_context(run, pair, before[left.chapter_number]), client, "cross_chapter")
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
                    "acceptance_contract": ACCEPTANCE_CONTRACT,
                    "developmental_chapters": len(chapters),
                    "identity_chapters": len(chapters),
                    "cross_chapter_pairs": max(0, (len(chapters) - 1) * (len(chapters) - 2) // 2),
                })
                save_report(session, run, settings, "passed", total_words=total)
                from ..schemas import ManuscriptQaReport
                return ManuscriptQaReport(
                    overall_verdict="Automatic editorial checks passed for the final manuscript.",
                    strengths=["Every final chapter passed an evidence-based editorial review.",
                               "Every adjacent chapter pair passed a review of its complete actual prose.",
                               "Every non-adjacent chapter pair passed complete-source continuity comparison.",
                               "Every chapter passed a separate developmental review in book context.",
                               "Whole-book chronology, canon, prose, and ending checks passed.",
                               "Chapter lengths and total manuscript word count passed deterministic checks."],
                    publication_readiness_label="automated checks passed",
                    publication_readiness_summary="See the automatic quality report for the exact reviewed manuscript and repair history.",
                )
            _apply_final_repairs(session, run, chapters, before, issues, settings, client, round_number)
            # All audit caches depend on the actual prose/context, so edited chapters and affected
            # neighbors are re-reviewed. Metadata will be reconstructed before that next sweep.
        raise AutonomousQualityError("Automatic editorial loop ended without acceptance.")
    except Exception as exc:
        save_report(session, run, settings, "not_passed", error=str(exc))
        raise
