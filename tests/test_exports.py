from __future__ import annotations

from docx import Document

from novel_generator.models import ChapterDraft, GenerationRun, Project
from novel_generator.services.exports import export_publication_artifact, export_run_artifacts, publication_export_options


def test_export_run_artifacts_writes_manuscript_and_qa_report_without_duplicate_headings(tmp_path) -> None:
    project = Project(
        title="The Glass Orchard",
        premise="A disgraced archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=2,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_provider_name="ollama",
        preferred_model="test-model",
        task_routing={},
    )
    run = GenerationRun(
        id="run-1",
        project_id="project-1",
        provider_name="ollama",
        model_name="test-model",
        target_word_count=2000,
        requested_chapters=2,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        task_routing={},
        current_step="completed",
    )
    chapters = [
        ChapterDraft(chapter_number=1, title="Arrival", outline_summary="Wake the map.", content="Chapter 1\n\nA beginning."),
        ChapterDraft(chapter_number=2, title="Descent", outline_summary="Enter the undercity.", content="Chapter 2: Descent\n\nA middle."),
    ]

    artifacts = export_run_artifacts(tmp_path, project, run, chapters, "# QA Report\n\n- Looks promising.")

    markdown = (tmp_path / "run-1" / "manuscript.md").read_text(encoding="utf-8")
    qa_report = (tmp_path / "run-1" / "qa-report.md").read_text(encoding="utf-8")

    assert len(artifacts) == 3
    assert (tmp_path / "run-1" / "manuscript.md").exists()
    assert (tmp_path / "run-1" / "manuscript.docx").exists()
    assert (tmp_path / "run-1" / "qa-report.md").exists()
    assert markdown.count("## Chapter 1: Arrival") == 1
    assert markdown.count("## Chapter 2: Descent") == 1
    assert "Chapter 1\n\nA beginning." not in markdown
    assert markdown.count("Chapter 2: Descent") == 1
    assert "A beginning." in markdown
    assert "A middle." in markdown
    assert "# QA Report" in qa_report


def test_publication_docx_profile_adds_front_matter_page_size_and_page_breaks(tmp_path) -> None:
    project = Project(
        title="The Glass Orchard",
        premise="A disgraced archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=2,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_provider_name="ollama",
        preferred_model="test-model",
        task_routing={},
    )
    run = GenerationRun(
        id="run-1",
        project_id="project-1",
        provider_name="ollama",
        model_name="test-model",
        target_word_count=2000,
        requested_chapters=2,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        task_routing={},
        current_step="completed",
    )
    chapters = [
        ChapterDraft(chapter_number=1, title="Arrival", outline_summary="Wake the map.", content="## Chapter 1\n\n**A beginning.**"),
        ChapterDraft(chapter_number=2, title="Descent", outline_summary="Enter the undercity.", content="Chapter 2: Descent\n\n`A middle.`"),
    ]

    artifact = export_publication_artifact(
        tmp_path,
        project,
        run,
        chapters,
        "print_6x9",
        include_ai_disclosure=True,
    )

    document = Document(tmp_path / artifact.relative_path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    xml = document._element.xml

    assert artifact.kind == "publication-docx"
    assert artifact.filename == "publication-print-6x9.docx"
    assert round(document.sections[0].page_width.inches, 1) == 6.0
    assert round(document.sections[0].page_height.inches, 1) == 9.0
    assert "Copyright (c) [Year] [Author Name]. All rights reserved." in text
    assert "Dedication" in text
    assert "Author Note" in text
    assert "AI-Assisted Disclosure" in text
    assert "Chapter 1: Arrival" in text
    assert "Chapter 2: Descent" in text
    assert "**" not in text
    assert "`" not in text
    assert xml.count('w:type="page"') >= 4


def test_publication_markdown_profile_keeps_qa_separate_from_publication_artifact(tmp_path) -> None:
    project = Project(
        title="The Glass Orchard",
        premise="A disgraced archivist finds a living map under a failing city.",
        desired_word_count=1000,
        requested_chapters=1,
        min_words_per_chapter=800,
        max_words_per_chapter=1200,
        preferred_provider_name="ollama",
        preferred_model="test-model",
        task_routing={},
    )
    run = GenerationRun(
        id="run-1",
        project_id="project-1",
        provider_name="ollama",
        model_name="test-model",
        target_word_count=1000,
        requested_chapters=1,
        min_words_per_chapter=800,
        max_words_per_chapter=1200,
        task_routing={},
        current_step="completed",
    )
    chapter = ChapterDraft(chapter_number=1, title="Arrival", outline_summary="Wake the map.", content="A beginning.")

    artifact = export_publication_artifact(tmp_path, project, run, [chapter], "ebook_markdown")
    text = (tmp_path / artifact.relative_path).read_text(encoding="utf-8")
    profile_ids = {profile.id for profile in publication_export_options()}

    assert {"ebook_markdown", "ebook_docx", "print_5x8", "print_6x9"}.issubset(profile_ids)
    assert artifact.kind == "publication-markdown"
    assert artifact.filename == "publication-ebook.md"
    assert "# QA Report" not in text
    assert "## Copyright" in text
    assert "## Chapter 1: Arrival" in text
