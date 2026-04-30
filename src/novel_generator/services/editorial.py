from __future__ import annotations

from collections import Counter
import re

from ..models import ChapterDraft
from ..schemas import ManuscriptQaReport


STOCK_PHRASES = [
    "the weight of",
    "raw emotions",
    "the colony breathed",
    "safety net",
    "violet ribbon",
    "mara stared",
]


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _opening_signature(content: str, words: int = 24) -> str:
    return " ".join(_normalized_words(content)[:words])


def lint_manuscript(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    if not chapters:
        return findings

    heading_duplicates = [
        chapter.chapter_number
        for chapter in chapters
        if re.match(r"^\s*chapter\s+\d+\b", (chapter.content or "").strip(), flags=re.IGNORECASE)
    ]
    if heading_duplicates:
        findings.append(
            "Duplicate chapter heading text remained inside chapter prose for chapters "
            + ", ".join(str(number) for number in heading_duplicates)
            + "."
        )

    openings = Counter()
    for chapter in chapters:
        signature = _opening_signature(chapter.content or "")
        if signature:
            openings[signature] += 1
    repeated_openings = [signature for signature, count in openings.items() if count > 1]
    if repeated_openings:
        findings.append("At least two chapters share nearly identical opening paragraphs.")

    manuscript_text = "\n\n".join(chapter.content or "" for chapter in chapters).lower()
    for phrase in STOCK_PHRASES:
        count = manuscript_text.count(phrase)
        if count >= 4:
            findings.append(f"Repeated stock phrase '{phrase}' appears {count} times.")

    for previous, current in zip(chapters, chapters[1:]):
        previous_words = set(_normalized_words(previous.summary or previous.content or ""))
        current_words = set(_normalized_words(current.summary or current.content or ""))
        if previous_words and current_words:
            overlap = len(previous_words & current_words) / max(1, len(current_words))
            if overlap >= 0.85:
                findings.append(
                    f"Chapter {current.chapter_number} appears to restate too much of chapter {previous.chapter_number}."
                )

    final_chapters = chapters[-3:]
    if len(final_chapters) >= 2:
        final_signatures = {_opening_signature(chapter.summary or chapter.content or "", words=16) for chapter in final_chapters}
        if len(final_signatures) < len(final_chapters):
            findings.append("The final act appears to repeat climax or ending beats.")

    for chapter in chapters:
        if not (chapter.summary or "").strip():
            findings.append(f"Chapter {chapter.chapter_number} is missing a continuity summary.")

    return findings


def render_qa_report_markdown(report: ManuscriptQaReport) -> str:
    sections = [
        "# Manuscript QA Report",
        "",
        f"**Overall verdict:** {report.overall_verdict}",
        "",
        "## Strengths",
        "",
        *([f"- {item}" for item in report.strengths] or ["- No strengths recorded."]),
        "",
        "## Warnings",
        "",
        *([f"- {item}" for item in report.warnings] or ["- No major warnings recorded."]),
        "",
        "## Continuity Risks",
        "",
        *([f"- {item}" for item in report.continuity_risks] or ["- No continuity risks recorded."]),
        "",
        "## Repetition Risks",
        "",
        *([f"- {item}" for item in report.repetition_risks] or ["- No repetition risks recorded."]),
        "",
        "## Ending Coherence Notes",
        "",
        *([f"- {item}" for item in report.ending_coherence_notes] or ["- No ending notes recorded."]),
        "",
        "## Deterministic Lint Findings",
        "",
        *([f"- {item}" for item in report.lint_findings] or ["- No deterministic lint findings recorded."]),
        "",
    ]
    return "\n".join(sections).strip() + "\n"
