from __future__ import annotations

from novel_generator.dependencies import get_session_factory
from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.repositories import create_project, create_run, get_run, recover_running_runs
from novel_generator.schemas import ProjectCreate, RunCreate
from novel_generator.services.pipeline import process_run_safe
from novel_generator.settings import get_settings


class FakeOllamaClient:
    def __init__(self):
        self.responses = iter(
            [
                "Chapter 1: Arrival | The archivist finds the map.\nChapter 2: Descent | The team enters the undercity.",
                "- Opening image\n- Discovery\n- Decision",
                "Chapter 1\n\nThe archivist finds the map and realizes it is alive.",
                "The archivist finds the living map and chooses to follow it underground.",
                "- The journey down\n- The first warning\n- Rising tension",
                "Chapter 2\n\nThe team enters the undercity and hears the city speak through its pipes.",
                "The team descends into the undercity and uncovers the first proof that the city itself remembers.",
            ]
        )

    def chat(self, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        return next(self.responses)


def test_process_run_safe_completes_and_exports(configured_environment) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        project = create_project(
            session,
            ProjectCreate(
                title="The Glass Orchard",
                premise="A disgraced archivist finds a living map under a failing city.",
                desired_word_count=2000,
                requested_chapters=2,
                min_words_per_chapter=900,
                max_words_per_chapter=1200,
                preferred_model="test-model",
            ),
        )
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        session.commit()
        run = get_run(session, run.id)
        process_run_safe(session, run, settings, FakeOllamaClient())

        refreshed = get_run(session, run.id)
        assert refreshed.status.value == "completed"
        assert len(refreshed.chapters) == 2
        assert all(chapter.status == ChapterStatus.COMPLETED for chapter in refreshed.chapters)
        assert all(chapter.content for chapter in refreshed.chapters)
        assert all(chapter.summary for chapter in refreshed.chapters)
        assert len(refreshed.artifacts) == 2
        assert refreshed.summary_context is not None


def test_recover_running_runs_marks_runs_as_queued(configured_environment) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        project = create_project(
            session,
            ProjectCreate(
                title="The Glass Orchard",
                premise="A disgraced archivist finds a living map under a failing city.",
                desired_word_count=2000,
                requested_chapters=2,
                min_words_per_chapter=900,
                max_words_per_chapter=1200,
                preferred_model="test-model",
            ),
        )
        run = create_run(session, project, RunCreate(project_id=project.id, model_name="test-model"))
        run.status = RunStatus.RUNNING
        session.commit()

        recovered = recover_running_runs(session)
        session.commit()

        refreshed = get_run(session, run.id)
        assert recovered == 1
        assert refreshed.status.value == "queued"
