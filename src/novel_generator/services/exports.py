from __future__ import annotations

from pathlib import Path

from docx import Document

from ..models import Artifact, ChapterDraft, GenerationRun, Project


def export_run_artifacts(
    artifacts_dir: Path,
    project: Project,
    run: GenerationRun,
    chapters: list[ChapterDraft],
) -> list[Artifact]:
    run_dir = artifacts_dir / run.id
    run_dir.mkdir(parents=True, exist_ok=True)

    markdown_name = "manuscript.md"
    markdown_path = run_dir / markdown_name
    markdown_path.write_text(render_markdown(project, chapters), encoding="utf-8")

    docx_name = "manuscript.docx"
    docx_path = run_dir / docx_name
    render_docx(project, chapters, docx_path)

    return [
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


def render_markdown(project: Project, chapters: list[ChapterDraft]) -> str:
    lines = [f"# {project.title}", "", project.premise, ""]
    for chapter in chapters:
        lines.extend(
            [
                f"## Chapter {chapter.chapter_number}: {chapter.title}",
                "",
                chapter.content or "",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_docx(project: Project, chapters: list[ChapterDraft], destination: Path) -> None:
    document = Document()
    document.add_heading(project.title, level=0)
    document.add_paragraph(project.premise)
    for chapter in chapters:
        document.add_heading(f"Chapter {chapter.chapter_number}: {chapter.title}", level=1)
        for paragraph in (chapter.content or "").split("\n\n"):
            if paragraph.strip():
                document.add_paragraph(paragraph.strip())
    document.save(destination)
