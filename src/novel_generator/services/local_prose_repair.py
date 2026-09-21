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


def repair_messages(text, span, issues):
    start, end = span
    return [
        {"role": "system", "content": (
            "Edit only the supplied passage to fix the diagnosed defect. Return the corrected passage "
            "as prose only, with no heading, explanation, or surrounding scenes. Preserve its unique "
            "facts, actions, and speaker attribution. For repetition, keep the statement once and "
            "remove its redundant occurrence; do not paraphrase the duplicate into another repetition."
        )},
        {"role": "user", "content": json.dumps({
            "passage_to_repair": text[start:end], "diagnoses": [issue.model_dump() for issue in issues],
        }, ensure_ascii=False)},
    ]


def apply_repair(text, span, replacement):
    start, end = span
    replacement = replacement.strip()
    if not replacement or " ".join(replacement.split()) == " ".join(text[start:end].split()):
        raise ValueError("Local repair produced no usable change.")
    if len(replacement.split()) > max(30, 2 * len(text[start:end].split())):
        raise ValueError("Local repair expanded beyond its bounded passage.")
    return text[:start] + replacement + text[end:]
