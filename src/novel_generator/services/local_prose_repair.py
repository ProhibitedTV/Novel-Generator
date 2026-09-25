"""Evidence-anchored edits that preserve all prose outside the diagnosed passage."""
from __future__ import annotations

import json
import re


def repair_span(text, issues):
    """Use local editing only for uniquely grounded, nearby surface defects."""
    if not issues or any(issue.category not in {"repetition", "prose"} for issue in issues):
        return None
    anchors = []
    for issue in issues:
        words = issue.evidence.split()
        if not words:
            return None
        matches = list(re.finditer(r"\s+".join(re.escape(word) for word in words), text))
        if len(matches) != 1:
            return None
        anchors.append(matches[0])
    first = min(item.start() for item in anchors)
    last = max(item.end() for item in anchors)
    # Include complete paragraphs so an edit cannot leave a broken sentence.
    separators = list(re.finditer(r"\r?\n[ \t\r\n]*\r?\n", text))
    start = max((item.end() for item in separators if item.end() <= first), default=0)
    end = min((item.start() for item in separators if item.start() >= last), default=len(text))
    if len(text[start:end].split()) > 250:
        return None
    return start, end


def repair_messages(text, span, issues, context=None):
    start, end = span
    maximum = max(30, 2 * len(text[start:end].split()))
    return [
        {"role": "system", "content": (
            "Edit only the supplied passage to fix the diagnosed defect. Return the corrected passage "
            "as prose only, with no heading, explanation, or surrounding scenes. Preserve its unique "
            "facts, actions, and speaker attribution. For repetition, keep the statement once and "
            "remove its redundant occurrence; do not paraphrase the duplicate into another repetition. "
            "Use read_only_context to understand causes and established facts, but never reproduce "
            "or modify it. For causality, make the missing causal connection explicit inside the "
            "supplied passage without inventing new events or changing the chapter's stopping state."
        )},
        {"role": "user", "content": json.dumps({
            "passage_to_repair": text[start:end], "diagnoses": [issue.model_dump() for issue in issues],
            "source_paragraph_start": len(re.split(r"\n\s*\n", text[:start].strip())) + 1 if text[:start].strip() else 1,
            "maximum_output_words": maximum,
            "read_only_context": context,
        }, ensure_ascii=False)},
    ]


def repair_plan(text, issues, max_passages=8):
    """Merge overlapping diagnoses, keeping distant passages independently editable."""
    separators = list(re.finditer(r"\r?\n[ \t\r\n]*\r?\n", text))
    paragraphs = [(start, end) for start, end in zip(
        [0] + [item.end() for item in separators],
        [item.start() for item in separators] + [len(text)]) if text[start:end].strip()]
    groups = []
    for issue in issues:
        # Eligibility depends on grounded, bounded source spans, not on which
        # editorial label the reviewer chooses for a localized defect.
        # Length changes still require chapter-wide expansion/compression.
        if issue.category not in {"prose", "repetition", "causality", "unresolved_payoff", "premature_payoff", "character", "continuity"}:
            return None
        references = getattr(issue, "evidence_paragraphs", [])
        if references:
            if references != sorted(set(references)) or any(index < 1 or index > len(paragraphs) for index in references):
                return None
            spans = [paragraphs[index - 1] for index in references]
        else:
            words = issue.evidence.split()
            if not words:
                return None
            matches = list(re.finditer(r"\s+".join(re.escape(word) for word in words), text))
            spans = [(start, end) for start, end in paragraphs
                     if any(match.start() < end and match.end() > start for match in matches)]
            if not spans:
                # Match the same quotation formatting accepted by the reviewer,
                # but only when every excerpt maps to actual source paragraphs.
                marks = str.maketrans("", "", "\"'‘’“”")
                normalize = lambda value: " ".join(value.translate(marks).split())
                pieces = [normalize(part) for part in re.split(r"\s*(?:\[\s*(?:\.{3}|…)\s*\]|\.{3}|…)\s*", issue.evidence) if part.strip()]
                spans = []
                for piece in pieces:
                    found = [span for span in paragraphs if piece and piece in normalize(text[slice(*span)])]
                    if not found:
                        return None
                    spans.extend(found)
                spans = sorted(set(spans))
        if not spans or any(len(text[start:end].split()) > 250 for start, end in spans):
            return None
        groups.extend((span, [issue]) for span in spans)
    groups.sort(key=lambda group: group[0])
    merged = []
    for span, diagnoses in groups:
        if merged and span[0] < merged[-1][0][1]:
            combined = merged[-1][1] + diagnoses
            combined_span = (merged[-1][0][0], max(merged[-1][0][1], span[1]))
            if len(text[slice(*combined_span)].split()) > 250:
                return None
            merged[-1] = (combined_span, combined)
        else:
            merged.append((span, diagnoses))
    return merged if 0 < len(merged) <= max_passages else None


def repair_passages(text, plan, generate, context=None):
    """Build an atomic candidate; failures leave the saved chapter untouched."""
    candidate = text
    # Descending original offsets remain valid as later passages change length.
    for number, (span, issues) in enumerate(reversed(plan), 1):
        messages = repair_messages(text, span, issues, context)
        for attempt in range(3):
            replacement = generate(messages, number, len(plan))
            if replacement.strip() and " ".join(replacement.split()) == " ".join(text[slice(*span)].split()):
                # The caller still rejects a completely unchanged chapter.
                break
            try:
                candidate = apply_repair(candidate, span, replacement)
                break
            except ValueError as exc:
                if attempt == 2:
                    raise
                # Keep the source/diagnosis, but do not feed oversized output back
                # into context or truncate prose mechanically.
                messages = repair_messages(text, span, issues, context)
                messages.append({"role": "user", "content": (
                    f"Your previous response was rejected: {exc} It contained {len(replacement.split())} words. "
                    f"Return only the repaired passage, 1-{max(30, 2 * len(text[slice(*span)].split()))} words. "
                    "Do not return the surrounding chapter."
                )})
    return candidate


def apply_repair(text, span, replacement):
    start, end = span
    replacement = replacement.strip()
    if not replacement or " ".join(replacement.split()) == " ".join(text[start:end].split()):
        raise ValueError("Local repair produced no usable change.")
    if len(replacement.split()) > max(30, 2 * len(text[start:end].split())):
        raise ValueError("Local repair expanded beyond its bounded passage.")
    return text[:start] + replacement + text[end:]
