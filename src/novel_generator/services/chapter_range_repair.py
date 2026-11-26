"""Source-addressed structural repairs when a full rewrite makes no progress."""
from __future__ import annotations

import json
import math
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .prompts import extract_json_payload
from .structured_schema_runtime import make_schema_marker


class NoUsableRangeRepair(ValueError):
    """Range proposals failed to make a substantive source change."""


class ChapterRangeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_paragraph: StrictInt = Field(ge=1)
    end_paragraph: StrictInt = Field(ge=1)
    replacement_text: str


class ChapterRangeEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[ChapterRangeEdit] = Field(min_length=1, max_length=16)


def paragraph_spans(text):
    separators = list(re.finditer(r"\r?\n[ \t\r\n]*\r?\n", text))
    return [(start, end) for start, end in zip(
        [0] + [item.end() for item in separators],
        [item.start() for item in separators] + [len(text)]) if text[start:end].strip()]


def apply_edits(text, payload, *, maximum_words):
    """Validate the complete edit set before applying original source offsets."""
    edits = ChapterRangeEdits.model_validate(payload).edits
    spans = paragraph_spans(text)
    ordered = sorted(edits, key=lambda edit: edit.start_paragraph)
    previous_end = 0
    for edit in ordered:
        if not edit.start_paragraph <= edit.end_paragraph <= len(spans):
            raise ValueError("Every range must be ordered and inside the selected chapter's source paragraphs.")
        if edit.start_paragraph <= previous_end:
            raise ValueError("Paragraph ranges must not overlap or repeat.")
        previous_end = edit.end_paragraph
    candidate = text
    for edit in reversed(ordered):
        start, end = spans[edit.start_paragraph - 1][0], spans[edit.end_paragraph - 1][1]
        candidate = candidate[:start] + edit.replacement_text.strip() + candidate[end:]
    if not candidate.strip():
        raise ValueError("Do not delete the entire chapter.")
    if " ".join(candidate.split()) == " ".join(text.split()):
        raise NoUsableRangeRepair("No usable change was made to the diagnosed source.")
    if len(candidate.split()) > maximum_words:
        raise ValueError(f"Repaired chapter exceeds the bounded {maximum_words}-word ceiling.")
    return candidate


def repair(text, issues, context, generate, *, validate_candidate=None):
    spans = paragraph_spans(text)
    schema = ChapterRangeEdits.model_json_schema()
    for field in ("start_paragraph", "end_paragraph"):
        schema["$defs"]["ChapterRangeEdit"]["properties"][field]["maximum"] = len(spans)
    maximum = max(context["chapter_word_range"][1], math.ceil(len(text.split()) * 1.25))
    source_context = {key: value for key, value in context.items() if key != "actual_prose"}
    system = (
        "Repair the selected chapter using source paragraph ranges. Return valid JSON only with an edits array; "
        "each edit has start_paragraph, end_paragraph, and replacement_text. Ranges are inclusive and refer ONLY "
        "to the original selected chapter, never another chapter or a previously proposed edit. Use at most 16 "
        "non-overlapping ranges. Unselected source is retained exactly by the application; do not copy it into edits. "
        "Fix all supplied diagnoses together. An empty replacement_text removes an erroneous or redundant scene. "
        "For missing material, replace the appropriate source range with corrected prose including that material. "
        "Preserve correct unique facts, voice, speaker identity, and author canon. The diagnosed erroneous events "
        "are allowed to change or disappear: preserving past events does not mean preserving a contradiction or "
        "a premature payoff. Honor the assigned chapter progression and stopping state. Other chapters are read-only. "
        "If removing a scene, repair the remaining transition or closing where needed. Do not summarize, renumber "
        "chapters, invent a new subplot, or pad to a word count. The complete result will receive fresh length, "
        "continuity, and manuscript audits; returning edits is not an acceptance decision."
    )
    from .book_specs import uses_book_contract, contract_text
    brief = context.get("author_brief", {})
    if uses_book_contract(brief):
        system += "\n" + contract_text(brief)
    messages = [make_schema_marker("autonomous_revision", schema=schema),
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({
            "source_paragraphs": [{"paragraph_number": i, "text": text[start:end]}
                                  for i, (start, end) in enumerate(spans, 1)],
            "diagnoses": [issue.model_dump() for issue in issues],
            "read_only_context": source_context, "maximum_chapter_words": maximum,
        }, ensure_ascii=False)}]
    for attempt in range(3):
        raw = generate(messages, attempt + 1)
        try:
            candidate = apply_edits(text, extract_json_payload(raw), maximum_words=maximum)
            if validate_candidate is not None:
                validate_candidate(candidate)
            return candidate
        except (ValueError, TypeError, KeyError) as exc:
            if attempt == 2:
                error_type = NoUsableRangeRepair if isinstance(exc, NoUsableRangeRepair) else ValueError
                raise error_type(f"Structural range repair could not produce usable edits: {exc}") from exc
            # Do not replay malformed or copied assistant output as a new source.
            messages = messages[:3] + [{"role": "user", "content": f"Correct the edit proposal: {exc}"}]
    raise AssertionError("Unreachable")


def repair_anchored_window(text, issues, context, generate, *, validate_candidate=None):
    """Last resort for copied range edits: replace one bounded, grounded window.

    A structural diagnosis may need more than its cited paragraph. Include one
    neighbor on either side, but never guess a location or silently rewrite an
    unbounded chapter. The caller must still re-audit the resulting prose.
    """
    from .local_prose_repair import repair_plan
    from .book_specs import uses_book_contract, contract_text

    # Scope is relaxed only for finding anchors; the original diagnoses and
    # their structural requirements remain in the prompt and subsequent audit.
    plan = repair_plan(text, [issue.model_copy(update={"repair_scope": "passage"})
                              for issue in issues], max_passages=16)
    if not plan:
        raise ValueError("No bounded, source-grounded window for fallback repair.")
    spans = paragraph_spans(text)
    indices = [i for i, (start, end) in enumerate(spans)
               if any(start < span[1] and end > span[0] for span, _ in plan)]
    if not indices:
        raise ValueError("No source paragraphs for fallback repair.")
    first, last = max(0, min(indices) - 1), min(len(spans) - 1, max(indices) + 1)
    start, end = spans[first][0], spans[last][1]
    original = text[start:end]
    words = len(original.split())
    if words > 650:
        raise ValueError("Diagnosed window is too broad for a bounded prose repair.")
    maximum = max(context["chapter_word_range"][1], math.ceil(len(text.split()) * 1.25))
    max_replacement = min(maximum - len(text[:start].split()) - len(text[end:].split()),
                          max(40, 2 * words))
    instruction = (
        "Correct the editable passage using the verified editorial diagnoses. "
        "Return ONLY the replacement prose for that passage, not JSON, a plan, or a chapter heading. "
        "An earlier paragraph-range proposal copied the source and was rejected. Make an actual "
        "correction to the diagnosed facts or events; changing whitespace is not a repair. "
        "Preserve correct facts, names, voice, and transitions to the read-only neighbors. "
        "Do not reproduce the neighbors. Do not invent a new subplot or perform a future chapter's payoff. "
        "If a required event is missing, add it inside the editable passage. The result will be independently re-audited."
    )
    brief = context.get("author_brief", {})
    if uses_book_contract(brief):
        instruction += "\n" + contract_text(brief)
    payload = {
        "editable_passage": original,
        "diagnoses": [issue.model_dump() for issue in issues],
        "read_only_before": text[max(0, start - 1200):start],
        "read_only_after": text[end:end + 1200],
        "read_only_context": {key: value for key, value in context.items() if key != "actual_prose"},
        "maximum_replacement_words": max_replacement,
    }
    messages = [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    reason = ""
    for attempt in range(1, 3):
        prompt = messages + ([{"role": "user", "content": f"Previous response rejected: {reason}. Correct the passage itself."}] if reason else [])
        replacement = generate(prompt, attempt).strip()
        try:
            if not replacement or " ".join(replacement.split()) == " ".join(original.split()):
                raise ValueError("No usable change to the editable passage.")
            if len(replacement.split()) > max_replacement:
                raise ValueError("Replacement exceeds the bounded passage word allowance.")
            candidate = text[:start] + replacement + text[end:]
            if len(candidate.split()) > maximum:
                raise ValueError("Repaired chapter exceeds the bounded word ceiling.")
            if validate_candidate is not None:
                validate_candidate(candidate)
            return candidate
        except ValueError as exc:
            reason = str(exc)
    raise ValueError(f"Anchored prose fallback could not produce usable edits: {reason}")
