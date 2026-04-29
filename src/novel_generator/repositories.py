from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Artifact,
    ChapterDraft,
    ChapterStatus,
    GenerationRun,
    Project,
    ProviderConfig,
    RunEvent,
    RunStatus,
)
from .schemas import ProjectCreate, ProjectUpdate, ProviderConfigUpdate, RunCreate
from .settings import Settings

TERMINAL_RUN_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}


def ensure_provider_config(session: Session, settings: Settings) -> ProviderConfig:
    config = session.scalar(select(ProviderConfig).where(ProviderConfig.provider_name == "ollama"))
    if config is None:
        config = ProviderConfig(
            provider_name="ollama",
            base_url=settings.ollama_base_url,
            default_model=settings.default_model,
            is_enabled=True,
        )
        session.add(config)
        session.flush()
    return config


def update_provider_config(session: Session, settings: Settings, payload: ProviderConfigUpdate) -> ProviderConfig:
    config = ensure_provider_config(session, settings)
    config.base_url = payload.base_url.rstrip("/")
    config.default_model = payload.default_model.strip()
    config.updated_at = datetime.utcnow()
    session.flush()
    return config


def list_projects(session: Session) -> list[Project]:
    stmt = (
        select(Project)
        .options(
            selectinload(Project.runs).selectinload(GenerationRun.artifacts),
            selectinload(Project.runs).selectinload(GenerationRun.chapters),
        )
        .order_by(Project.created_at.desc())
    )
    return list(session.scalars(stmt).unique())


def get_project(session: Session, project_id: str) -> Project | None:
    stmt = (
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.runs).selectinload(GenerationRun.artifacts),
            selectinload(Project.runs).selectinload(GenerationRun.chapters),
            selectinload(Project.runs).selectinload(GenerationRun.events),
        )
    )
    return session.scalar(stmt)


def create_project(session: Session, payload: ProjectCreate) -> Project:
    project = Project(**payload.model_dump())
    session.add(project)
    session.flush()
    return project


def update_project(session: Session, project: Project, payload: ProjectUpdate) -> Project:
    updates = payload.model_dump(exclude_unset=True)
    if "notes" in updates and updates["notes"] == "":
        updates["notes"] = None
    for key, value in updates.items():
        setattr(project, key, value)
    project.updated_at = datetime.utcnow()
    session.flush()
    return project


def get_run(session: Session, run_id: str) -> GenerationRun | None:
    stmt = (
        select(GenerationRun)
        .where(GenerationRun.id == run_id)
        .options(
            selectinload(GenerationRun.project),
            selectinload(GenerationRun.chapters),
            selectinload(GenerationRun.artifacts),
            selectinload(GenerationRun.events),
        )
    )
    return session.scalar(stmt)


def list_recent_runs(session: Session, limit: int = 6) -> list[GenerationRun]:
    stmt = (
        select(GenerationRun)
        .options(
            selectinload(GenerationRun.project),
            selectinload(GenerationRun.artifacts),
            selectinload(GenerationRun.chapters),
        )
        .order_by(GenerationRun.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).unique())


def get_run_minimal(session: Session, run_id: str) -> GenerationRun | None:
    return session.scalar(select(GenerationRun).where(GenerationRun.id == run_id))


def get_run_for_processing(session: Session, run_id: str) -> GenerationRun | None:
    stmt = (
        select(GenerationRun)
        .where(GenerationRun.id == run_id)
        .options(
            selectinload(GenerationRun.project),
            selectinload(GenerationRun.chapters),
            selectinload(GenerationRun.artifacts),
        )
    )
    return session.scalar(stmt)


def get_artifact(session: Session, artifact_id: str) -> Artifact | None:
    return session.scalar(select(Artifact).where(Artifact.id == artifact_id))


def create_run(session: Session, project: Project, payload: RunCreate) -> GenerationRun:
    model_name = (payload.model_name or project.preferred_model).strip()
    target_word_count = payload.target_word_count or project.desired_word_count
    requested_chapters = payload.requested_chapters or project.requested_chapters
    min_words_per_chapter = payload.min_words_per_chapter or project.min_words_per_chapter
    max_words_per_chapter = payload.max_words_per_chapter or project.max_words_per_chapter

    if not model_name:
        raise ValueError("A model name is required.")
    if max_words_per_chapter < min_words_per_chapter:
        raise ValueError("Max words per chapter must be greater than or equal to min words per chapter.")

    run = GenerationRun(
        project_id=project.id,
        model_name=model_name,
        target_word_count=target_word_count,
        requested_chapters=requested_chapters,
        min_words_per_chapter=min_words_per_chapter,
        max_words_per_chapter=max_words_per_chapter,
        status=RunStatus.QUEUED,
        current_step="queued",
        source_run_id=payload.source_run_id,
        resume_from_chapter=payload.resume_from_chapter,
    )
    session.add(run)
    session.flush()

    if payload.source_run_id and payload.resume_from_chapter:
        source_run = get_run(session, payload.source_run_id)
        if source_run is None:
            raise ValueError("Source run was not found.")
        if source_run.project_id != project.id:
            raise ValueError("Source run must belong to the same project.")
        if not source_run.outline:
            raise ValueError("Source run does not have a reusable outline.")
        run.outline = deepcopy(source_run.outline)
        for source_chapter in source_run.chapters:
            cloned = ChapterDraft(
                run=run,
                chapter_number=source_chapter.chapter_number,
                title=source_chapter.title,
                outline_summary=source_chapter.outline_summary,
            )
            session.add(cloned)
            if source_chapter.chapter_number < payload.resume_from_chapter:
                cloned.plan = source_chapter.plan
                cloned.content = source_chapter.content
                cloned.summary = source_chapter.summary
                cloned.status = source_chapter.status
                cloned.word_count = source_chapter.word_count
        session.flush()

    return run


def record_event(session: Session, run: GenerationRun, event_type: str, payload: dict) -> RunEvent:
    next_sequence = (
        session.scalar(
            select(func.coalesce(func.max(RunEvent.sequence), 0) + 1).where(RunEvent.run_id == run.id)
        )
        or 1
    )
    event = RunEvent(run_id=run.id, sequence=next_sequence, event_type=event_type, payload=payload)
    session.add(event)
    session.flush()
    return event


def list_events_after(session: Session, run_id: str, after_sequence: int) -> list[RunEvent]:
    stmt = (
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.sequence > after_sequence)
        .order_by(RunEvent.sequence.asc())
    )
    return list(session.scalars(stmt))


def replace_artifacts(session: Session, run: GenerationRun, artifacts: list[Artifact]) -> None:
    for artifact in list(run.artifacts):
        session.delete(artifact)
    session.flush()
    for artifact in artifacts:
        artifact.run_id = run.id
        session.add(artifact)
    session.flush()
    session.refresh(run, attribute_names=["artifacts"])


def delete_run(session: Session, run: GenerationRun) -> str:
    run_id = run.id
    session.delete(run)
    session.flush()
    return run_id


def delete_terminal_runs_for_project(session: Session, project: Project) -> list[str]:
    deleted_run_ids: list[str] = []
    for run in list(project.runs):
        if run.status in TERMINAL_RUN_STATUSES:
            deleted_run_ids.append(run.id)
            session.delete(run)
    session.flush()
    return deleted_run_ids


def delete_project(session: Session, project: Project) -> list[str]:
    run_ids = [run.id for run in project.runs]
    session.delete(project)
    session.flush()
    return run_ids


def create_chapters_from_outline(session: Session, run: GenerationRun) -> list[ChapterDraft]:
    existing = {chapter.chapter_number: chapter for chapter in run.chapters}
    created: list[ChapterDraft] = []
    for index, item in enumerate(run.outline or [], start=1):
        chapter = existing.get(index)
        if chapter is None:
            chapter = ChapterDraft(
                run=run,
                chapter_number=index,
                title=item["title"],
                outline_summary=item["summary"],
                status=ChapterStatus.PENDING,
            )
            session.add(chapter)
            created.append(chapter)
        else:
            chapter.title = item["title"]
            chapter.outline_summary = item["summary"]
    session.flush()
    session.refresh(run, attribute_names=["chapters"])
    return created


def claim_next_queued_run(session: Session) -> GenerationRun | None:
    stmt = (
        select(GenerationRun)
        .where(GenerationRun.status == RunStatus.QUEUED, GenerationRun.cancel_requested.is_(False))
        .order_by(GenerationRun.created_at.asc())
        .limit(1)
    )
    run = session.scalar(stmt)
    if run is None:
        return None
    run.status = RunStatus.RUNNING
    run.current_step = "starting"
    run.started_at = run.started_at or datetime.utcnow()
    run.error_message = None
    session.flush()
    record_event(session, run, "run_started", {"message": "Worker claimed the run."})
    return run


def recover_running_runs(session: Session) -> int:
    runs = list(session.scalars(select(GenerationRun).where(GenerationRun.status == RunStatus.RUNNING)))
    for run in runs:
        run.status = RunStatus.QUEUED
        run.current_step = "recovered"
        record_event(
            session,
            run,
            "run_requeued",
            {"message": "Run was re-queued after an interrupted worker."},
        )
    return len(runs)
