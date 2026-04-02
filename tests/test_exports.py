from __future__ import annotations

from novel_generator.models import ChapterDraft, GenerationRun, Project
from novel_generator.services.exports import export_run_artifacts


def test_export_run_artifacts_writes_markdown_and_docx(tmp_path) -> None:
    project = Project(
        title="The Glass Orchard",
        premise="A disgraced archivist finds a living map under a failing city.",
        desired_word_count=2000,
        requested_chapters=2,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        preferred_model="test-model",
    )
    run = GenerationRun(
        id="run-1",
        project_id="project-1",
        model_name="test-model",
        target_word_count=2000,
        requested_chapters=2,
        min_words_per_chapter=900,
        max_words_per_chapter=1200,
        current_step="completed",
    )
    chapters = [
        ChapterDraft(chapter_number=1, title="Arrival", outline_summary="Wake the map.", content="Chapter 1\n\nA beginning."),
        ChapterDraft(chapter_number=2, title="Descent", outline_summary="Enter the undercity.", content="Chapter 2\n\nA middle."),
    ]

    artifacts = export_run_artifacts(tmp_path, project, run, chapters)

    assert len(artifacts) == 2
    assert (tmp_path / "run-1" / "manuscript.md").exists()
    assert (tmp_path / "run-1" / "manuscript.docx").exists()
    assert "# The Glass Orchard" in (tmp_path / "run-1" / "manuscript.md").read_text(encoding="utf-8")
