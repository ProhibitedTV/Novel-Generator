from __future__ import annotations

from novel_generator.models import ChapterDraft, GenerationRun, Project
from novel_generator.services.exports import export_run_artifacts


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
