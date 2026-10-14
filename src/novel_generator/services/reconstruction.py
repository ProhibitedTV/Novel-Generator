"""Queue an isolated revised edition from a preserved manuscript and revision plan."""
from copy import deepcopy

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..models import Artifact, GenerationRun, RunStatus
from ..repositories import create_project, create_run, get_run, record_event
from ..schemas import ProjectCreate, RunCreate, StoryBrief, TaskRouting
from .autonomous_editorial import _hash
from .book_specs import run_book_brief


class ReconstructionRequest(BaseModel):
    revision_plan: str = Field(min_length=10, max_length=24000)
    brief_updates: dict = Field(default_factory=dict)
    task_routing: TaskRouting | None = None


def queue_reconstruction(session, source, request: ReconstructionRequest, settings):
    if source.status != RunStatus.COMPLETED or any(not c.content for c in source.chapters):
        raise ValueError("A reconstruction requires a completed source manuscript.")
    if [c.chapter_number for c in source.chapters] != list(range(1, source.requested_chapters + 1)):
        raise ValueError("A reconstruction requires all source chapters in order.")
    unknown = set(request.brief_updates) - set(StoryBrief.model_fields)
    if unknown:
        raise ValueError("Unknown author-brief fields: " + ", ".join(sorted(unknown)))
    brief = StoryBrief.model_validate({**deepcopy(run_book_brief(source)), **request.brief_updates})
    routing = request.task_routing.model_dump(exclude_none=True) if request.task_routing is not None else deepcopy(source.task_routing)
    source_hash = _hash([(c.chapter_number, c.content) for c in source.chapters])
    fingerprint = _hash({"contract": 1, "source_hash": source_hash, "source_run_id": source.id, "plan": request.revision_plan,
                         "brief": brief.model_dump(), "routing": routing,
                         "source_settings": [source.project.title, source.project.premise, source.project.notes,
                             source.provider_name, source.model_name, source.requested_chapters, source.target_word_count,
                             source.min_words_per_chapter, source.max_words_per_chapter]})
    for candidate in session.scalars(select(GenerationRun).where(GenerationRun.source_run_id == source.id)):
        if candidate.status == RunStatus.CANCELED:
            continue
        existing = get_run(session, candidate.id)
        if any(e.event_type == "reconstruction_queued" and e.payload.get("fingerprint") == fingerprint for e in existing.events):
            return existing
    project = create_project(session, ProjectCreate(
        title=source.project.title, premise=source.project.premise,
        desired_word_count=source.target_word_count, requested_chapters=source.requested_chapters,
        min_words_per_chapter=source.min_words_per_chapter, max_words_per_chapter=source.max_words_per_chapter,
        preferred_provider_name=source.provider_name, preferred_model=source.model_name,
        notes=(source.project.notes or "") + "\n\nReconstruction plan (planning instructions, not manuscript text):\n" + request.revision_plan,
        story_brief=brief, task_routing=routing,
    ))
    run = create_run(session, project, RunCreate(project_id=project.id, source_run_id=source.id,
        quality_profile="autonomous", pause_after_outline=False, task_routing=routing))
    directory = settings.artifacts_dir / run.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reconstruction-plan.md"
    path.write_text(f"# Revised edition of {source.project.title}\n\nSource run: {source.id}\n\n"
                    f"Source manuscript hash: {source_hash}\n\n" + request.revision_plan, encoding="utf-8")
    session.add(Artifact(run_id=run.id, kind="reconstruction-plan", filename=path.name,
        relative_path=path.relative_to(settings.artifacts_dir).as_posix(), content_type="text/markdown"))
    record_event(session, run, "reconstruction_queued", {
        "message": "Queued a fresh revised edition with an independent brief and reconstruction plan; original prose preserved.",
        "source_run_id": source.id, "source_manuscript_hash": source_hash, "fingerprint": fingerprint})
    session.commit()
    return get_run(session, run.id)
