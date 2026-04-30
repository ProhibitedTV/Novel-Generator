from __future__ import annotations

from pathlib import Path

from docx import Document

from ..models import Artifact, ChapterDraft, GenerationRun, Project
from .prompts import sanitize_chapter_content


def export_run_artifacts(
    artifacts_dir: Path,
    project: Project,
    run: GenerationRun,
    chapters: list[ChapterDraft],
    qa_report_markdown: str | None = None,
) -> list[Artifact]:
    run_dir = artifacts_dir / run.id
    run_dir.mkdir(parents=True, exist_ok=True)

    markdown_name = "manuscript.md"
    markdown_path = run_dir / markdown_name
    markdown_path.write_text(render_markdown(project, chapters), encoding="utf-8")

    docx_name = "manuscript.docx"
    docx_path = run_dir / docx_name
    render_docx(project, chapters, docx_path)

    artifacts = [
        Artifact(
            kind="markdown",
            filename=markdown_name,
            relative_path=str(markdown_path.relative_to(artifacts_dir)),
            content_type="text/markdown",
        ),
        Artifact(
            kind="docx",
            filename=docx_name,
            relative_path=str(docx_path.relative_to(artifacts_dir)),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]
    if qa_report_markdown is not None:
        qa_name = "qa-report.md"
        qa_path = run_dir / qa_name
        qa_path.write_text(qa_report_markdown, encoding="utf-8")
        artifacts.append(
            Artifact(
                kind="qa-report",
                filename=qa_name,
                relative_path=str(qa_path.relative_to(artifacts_dir)),
                content_type="text/markdown",
            )
        )
    return artifacts


def render_markdown(project: Project, chapters: list[ChapterDraft]) -> str:
    lines = [f"# {project.title}", "", project.premise, ""]
    for chapter in chapters:
        content = sanitize_chapter_content(chapter.content or "")
        lines.extend(
            [
                f"## Chapter {chapter.chapter_number}: {chapter.title}",
                "",
                content,
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_docx(project: Project, chapters: list[ChapterDraft], destination: Path) -> None:
    document = Document()
    document.add_heading(project.title, level=0)
    document.add_paragraph(project.premise)
    for chapter in chapters:
        content = sanitize_chapter_content(chapter.content or "")
        document.add_heading(f"Chapter {chapter.chapter_number}: {chapter.title}", level=1)
        for paragraph in content.split("\n\n"):
            if paragraph.strip():
                document.add_paragraph(paragraph.strip())
    document.save(destination)
