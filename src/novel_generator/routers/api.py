from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..dependencies import get_app_settings, get_db, get_session_factory
from ..models import RunStatus
from ..repositories import (
    create_project,
    create_run,
    delete_project,
    delete_run,
    delete_terminal_runs_for_project,
    ensure_provider_config,
    ensure_provider_configs,
    get_artifact,
    get_project,
    get_run,
    get_run_minimal,
    list_events_after,
    record_event,
    update_project,
)
from ..schemas import (
    GenerationRunRead,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ProviderCapabilities,
    ProviderConfigRead,
    ProviderConfigUpdate,
    RunCreate,
)
from ..services.provider_errors import ProviderError, ProviderTransportError
from ..services.providers import ProviderManager, provider_definition
from ..services.state import approve_outline_review, request_run_cancellation
from ..services.storage import delete_run_artifacts_dir, delete_run_artifacts_dirs
from ..settings import Settings

router = APIRouter(tags=["api"])
TERMINAL_RUN_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}


def _provider_manager(settings: Settings, db: Session) -> ProviderManager:
    configs = ensure_provider_configs(db, settings)
    db.commit()
    return ProviderManager(settings, configs)


def _provider_config_read(config) -> ProviderConfigRead:
    return ProviderConfigRead(
        provider_name=config.provider_name,
        base_url=config.base_url,
        default_model=config.default_model,
        api_key_set=bool(config.api_key),
        is_enabled=config.is_enabled,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _normalized_task_routing(raw_routing) -> dict:
    if raw_routing is None:
        return {}
    if hasattr(raw_routing, "model_dump"):
        return raw_routing.model_dump(exclude_none=True)
    return dict(raw_routing)


def _validate_routing_models(
    manager: ProviderManager,
    default_provider_name: str,
    default_model_name: str,
    task_routing: dict,
) -> None:
    routes = {(default_provider_name.strip(), default_model_name.strip())}
    for stage, override in (task_routing or {}).items():
        if not override:
            continue
        provider_name = str(override.get("provider_name") or default_provider_name).strip()
        model_name = str(override.get("model_name") or default_model_name).strip()
        if not provider_name:
            raise ProviderError(f"No provider was configured for stage '{stage}'.")
        if not model_name:
            raise ProviderError(f"No model was configured for stage '{stage}'.")
        config = manager.config_for(provider_name)
        if not config.is_enabled:
            label = provider_definition(provider_name).label
            raise ProviderError(f"{label} is disabled. Enable it in provider settings before using it in a run.")
        routes.add((provider_name, model_name))

    for provider_name, model_name in routes:
        manager.ensure_model(provider_name, model_name)


def _validated_project_update(project, payload: ProjectUpdate) -> ProjectUpdate:
    merged = {
        "title": project.title,
        "premise": project.premise,
        "desired_word_count": project.desired_word_count,
        "requested_chapters": project.requested_chapters,
        "min_words_per_chapter": project.min_words_per_chapter,
        "max_words_per_chapter": project.max_words_per_chapter,
        "preferred_provider_name": project.preferred_provider_name,
        "preferred_model": project.preferred_model,
        "notes": project.notes,
        "story_brief": project.story_brief or {},
        "task_routing": project.task_routing or {},
    }
    merged.update(payload.model_dump(exclude_unset=True))
    validated = ProjectCreate.model_validate(merged)
    return ProjectUpdate(**validated.model_dump())


def _same_settings_payload(run, *, pause_after_outline: bool = True) -> RunCreate:
    return RunCreate(
        project_id=run.project_id,
        provider_name=run.provider_name,
        model_name=run.model_name,
        target_word_count=run.target_word_count,
        requested_chapters=run.requested_chapters,
        min_words_per_chapter=run.min_words_per_chapter,
        max_words_per_chapter=run.max_words_per_chapter,
        pause_after_outline=pause_after_outline,
        task_routing=run.task_routing or {},
    )


@router.get("/health")
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> dict:
    manager = _provider_manager(settings, db)
    primary_provider = manager.health("ollama")
    providers = [manager.health(provider_name).model_dump() for provider_name in ("ollama", "openai_compatible")]
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "database": "ok",
        "provider": primary_provider.model_dump(),
        "providers": providers,
    }


@router.get("/providers", response_model=list[ProviderConfigRead])
def list_provider_configs_api(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> list[ProviderConfigRead]:
    return [_provider_config_read(config) for config in ensure_provider_configs(db, settings)]


@router.get("/providers/{provider_name}/status", response_model=ProviderCapabilities)
def provider_status(
    provider_name: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ProviderCapabilities:
    manager = _provider_manager(settings, db)
    try:
        return manager.health(provider_name)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/providers/ollama/status", response_model=ProviderCapabilities)
def ollama_status(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> ProviderCapabilities:
    return provider_status("ollama", db, settings)


@router.get("/providers/{provider_name}/models")
def provider_models(provider_name: str, db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> dict:
    manager = _provider_manager(settings, db)
    try:
        return {"models": manager.list_models(provider_name)}
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/providers/ollama/models")
def ollama_models(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> dict:
    return provider_models("ollama", db, settings)


@router.post("/providers/{provider_name}/config", response_model=ProviderCapabilities)
def update_provider_api(
    provider_name: str,
    payload: ProviderConfigUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ProviderCapabilities:
    from ..repositories import update_provider_config

    try:
        config = update_provider_config(db, settings, provider_name, payload)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    manager = _provider_manager(settings, db)
    return manager.health(config.provider_name)


@router.post("/providers/ollama/config", response_model=ProviderCapabilities)
def update_ollama_config(
    payload: ProviderConfigUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ProviderCapabilities:
    return update_provider_api("ollama", payload, db, settings)


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def api_create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectRead:
    project = create_project(db, payload)
    db.commit()
    project = get_project(db, project.id)
    return ProjectRead.model_validate(project)


@router.get("/projects/{project_id}", response_model=ProjectRead)
def api_get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectRead:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return ProjectRead.model_validate(project)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def api_update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> ProjectRead:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        validated = _validated_project_update(project, payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    project = update_project(db, project, validated)
    db.commit()
    project = get_project(db, project.id)
    return ProjectRead.model_validate(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if any(run.status not in TERMINAL_RUN_STATUSES for run in project.runs):
        raise HTTPException(status_code=409, detail="Cancel or finish active runs before deleting this project.")

    run_ids = delete_project(db, project)
    db.commit()
    delete_run_artifacts_dirs(settings.artifacts_dir, run_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/projects/{project_id}/runs/terminal")
def api_delete_terminal_runs_for_project(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, int]:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    deleted_run_ids = delete_terminal_runs_for_project(db, project)
    db.commit()
    delete_run_artifacts_dirs(settings.artifacts_dir, deleted_run_ids)
    return {"deleted_runs": len(deleted_run_ids)}


@router.post("/runs", response_model=GenerationRunRead, status_code=status.HTTP_201_CREATED)
def api_create_run(
    payload: RunCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GenerationRunRead:
    project = get_project(db, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    manager = _provider_manager(settings, db)
    provider_name = (payload.provider_name or project.preferred_provider_name or "ollama").strip()
    try:
        provider_config = manager.config_for(provider_name)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    model_name = (payload.model_name or project.preferred_model or provider_config.default_model).strip()
    task_routing = _normalized_task_routing(payload.task_routing) or dict(project.task_routing or {})
    if not model_name:
        raise HTTPException(status_code=400, detail="A model name is required.")
    try:
        _validate_routing_models(manager, provider_name, model_name, task_routing)
    except ProviderTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = payload.model_copy(update={"provider_name": provider_name, "model_name": model_name, "task_routing": task_routing})
    try:
        run = create_run(db, project, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_event(
        db,
        run,
        "run_queued",
        {"message": "Run queued for processing.", "provider_name": provider_name, "model_name": model_name},
    )
    db.commit()
    run = get_run(db, run.id)
    return GenerationRunRead.model_validate(run)


@router.get("/runs/{run_id}", response_model=GenerationRunRead)
def api_get_run(run_id: str, db: Session = Depends(get_db)) -> GenerationRunRead:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return GenerationRunRead.model_validate(run)


@router.post("/runs/{run_id}/cancel", response_model=GenerationRunRead)
def api_cancel_run(run_id: str, db: Session = Depends(get_db)) -> GenerationRunRead:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    request_run_cancellation(db, run)
    db.commit()
    run = get_run(db, run.id)
    return GenerationRunRead.model_validate(run)


@router.post("/runs/{run_id}/approve-outline", response_model=GenerationRunRead)
def api_approve_outline(run_id: str, db: Session = Depends(get_db)) -> GenerationRunRead:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        approve_outline_review(db, run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    run = get_run(db, run.id)
    return GenerationRunRead.model_validate(run)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_run(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status not in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Only completed, failed, or canceled runs can be deleted.")

    deleted_run_id = delete_run(db, run)
    db.commit()
    delete_run_artifacts_dir(settings.artifacts_dir, deleted_run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runs/{run_id}/rerun", response_model=GenerationRunRead, status_code=status.HTTP_201_CREATED)
def api_rerun(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GenerationRunRead:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status not in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Wait for the current run to finish before re-queueing it.")

    manager = _provider_manager(settings, db)
    try:
        _validate_routing_models(manager, run.provider_name, run.model_name, run.task_routing or {})
    except ProviderTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = _same_settings_payload(run, pause_after_outline=True)
    new_run = create_run(db, run.project, payload)
    record_event(
        db,
        new_run,
        "run_queued",
        {"message": "Run re-queued with the same settings.", "provider_name": run.provider_name, "model_name": run.model_name},
    )
    db.commit()
    new_run = get_run(db, new_run.id)
    return GenerationRunRead.model_validate(new_run)


@router.get("/runs/{run_id}/events")
async def api_run_events(run_id: str) -> StreamingResponse:
    session_factory = get_session_factory()

    async def event_stream():
        last_sequence = 0
        while True:
            with session_factory() as session:
                run = get_run_minimal(session, run_id)
                if run is None:
                    yield "event: error\ndata: {\"message\":\"Run not found.\"}\n\n"
                    return
                events = list_events_after(session, run_id, last_sequence)
                for event in events:
                    last_sequence = event.sequence
                    payload = {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                        "run_status": run.status.value,
                        "current_step": run.current_step,
                        "current_chapter": run.current_chapter,
                    }
                    yield f"id: {event.sequence}\ndata: {json.dumps(payload)}\n\n"

                terminal = run.status in TERMINAL_RUN_STATUSES
                if terminal and not events:
                    final_payload = {
                        "run_status": run.status.value,
                        "current_step": run.current_step,
                        "current_chapter": run.current_chapter,
                    }
                    yield f"event: terminal\ndata: {json.dumps(final_payload)}\n\n"
                    return
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/artifacts/{artifact_id}/download")
def api_download_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    artifact = get_artifact(db, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    artifact_path = settings.artifacts_dir / artifact.relative_path
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact file is missing from storage.")
    return FileResponse(artifact_path, media_type=artifact.content_type, filename=artifact.filename)
