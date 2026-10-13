"""Source-addressed structural repairs when a full rewrite makes no progress."""
from __future__ import annotations

import json
import math
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .prompts import extract_json_payload
from .structured_schema_runtime import make_schema_marker


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
        raise ValueError("No usable change was made to the diagnosed source.")
    if len(candidate.split()) > maximum_words:
        raise ValueError(f"Repaired chapter exceeds the bounded {maximum_words}-word ceiling.")
    return candidate


def repair(text, issues, context, generate):
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
            return apply_edits(text, extract_json_payload(raw), maximum_words=maximum)
        except (ValueError, TypeError, KeyError) as exc:
            if attempt == 2:
                raise ValueError(f"Structural range repair could not produce usable edits: {exc}") from exc
            # Do not replay malformed or copied assistant output as a new source.
            messages = messages[:3] + [{"role": "user", "content": f"Correct the edit proposal: {exc}"}]
    raise AssertionError("Unreachable")
