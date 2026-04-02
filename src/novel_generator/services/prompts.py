from __future__ import annotations

import re

from ..models import ChapterDraft, GenerationRun, Project


def build_outline_messages(project: Project, run: GenerationRun) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a meticulous fiction outliner. Return compact, practical planning output "
                "that helps a later drafting step write a coherent novel."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Create an outline for a novel titled '{project.title}'. Premise: {project.premise}\n"
                f"Total target words: {run.target_word_count}. Requested chapters: {run.requested_chapters}.\n"
                "Return exactly one line per chapter in this format:\n"
                "Chapter N: Title | One-sentence summary"
            ),
        },
    ]


def parse_outline(text: str, requested_chapters: int) -> list[dict[str, str]]:
    lines = [line.strip("-* \t") for line in text.splitlines() if line.strip()]
    outline: list[dict[str, str]] = []
    for index, line in enumerate(lines, start=1):
        cleaned = re.sub(r"^\s*(chapter\s*)?\d+[\.\:\-\)]\s*", "", line, flags=re.IGNORECASE).strip()
        if not cleaned:
            continue
        title = f"Chapter {index}"
        summary = cleaned
        for separator in ("|", " - ", ": "):
            if separator in cleaned:
                left, right = cleaned.split(separator, 1)
                title = left.strip() or title
                summary = right.strip() or summary
                break
        else:
            title = cleaned[:60].strip() or title
        outline.append({"title": title, "summary": summary})
        if len(outline) == requested_chapters:
            break

    while len(outline) < requested_chapters:
        chapter_number = len(outline) + 1
        outline.append(
            {
                "title": f"Chapter {chapter_number}",
                "summary": "Advance the central conflict while deepening the cast and stakes.",
            }
        )
    return outline


def rolling_context(chapters: list[ChapterDraft], window: int) -> str:
    completed = [chapter for chapter in chapters if chapter.summary]
    recent = completed[-window:]
    if not recent:
        return "No previous chapters yet."
    return "\n".join(
        f"Chapter {chapter.chapter_number}: {chapter.summary}"
        for chapter in recent
    )


def build_chapter_plan_messages(
    project: Project,
    run: GenerationRun,
    chapter: ChapterDraft,
    prior_context: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "You plan fiction scenes. Return concise bullet points only.",
        },
        {
            "role": "user",
            "content": (
                f"Novel premise: {project.premise}\n"
                f"Current chapter: {chapter.chapter_number} - {chapter.title}\n"
                f"Chapter purpose: {chapter.outline_summary}\n"
                f"Target chapter word range: {run.min_words_per_chapter}-{run.max_words_per_chapter}\n"
                f"Recent chapter context:\n{prior_context}\n\n"
                "Write 4-6 bullet points that describe the key beats, character turns, and cliffhanger or landing."
            ),
        },
    ]


def build_chapter_draft_messages(
    project: Project,
    run: GenerationRun,
    chapter: ChapterDraft,
    prior_context: str,
) -> list[dict[str, str]]:
    plan = chapter.plan or "Use the outline summary to guide the chapter."
    return [
        {
            "role": "system",
            "content": (
                "You write vivid, coherent fiction chapters. Keep continuity strong, avoid repetition, "
                "and do not include commentary outside the prose."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Title: {project.title}\n"
                f"Premise: {project.premise}\n"
                f"Write chapter {chapter.chapter_number} titled '{chapter.title}'.\n"
                f"Chapter goal: {chapter.outline_summary}\n"
                f"Target word range: {run.min_words_per_chapter}-{run.max_words_per_chapter}\n"
                f"Continuity notes:\n{prior_context}\n\n"
                f"Beat plan:\n{plan}\n\n"
                "Return the chapter prose only. Include a chapter heading on the first line."
            ),
        },
    ]


def build_summary_messages(chapter: ChapterDraft) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "You summarize fiction chapters for continuity memory.",
        },
        {
            "role": "user",
            "content": (
                f"Summarize chapter {chapter.chapter_number} in 3-5 sentences.\n\n"
                f"{chapter.content or ''}"
            ),
        },
    ]
