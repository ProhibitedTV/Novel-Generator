from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..dependencies import get_app_settings, get_db, get_session_factory
from ..models import RunStatus
from ..repositories import (
    create_project,
    create_run,
    ensure_provider_config,
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
    ProviderConfigUpdate,
    RunCreate,
)
from ..services.ollama import OllamaClient, OllamaError, OllamaTransportError
from ..services.state import request_run_cancellation
from ..settings import Settings

router = APIRouter(tags=["api"])


def _provider_client(settings: Settings, db: Session) -> tuple[OllamaClient, str]:
    config = ensure_provider_config(db, settings)
    db.commit()
    client = OllamaClient(
        base_url=config.base_url,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
    return client, config.default_model


def _validated_project_update(project, payload: ProjectUpdate) -> ProjectUpdate:
    merged = {
        "title": project.title,
        "premise": project.premise,
        "desired_word_count": project.desired_word_count,
        "requested_chapters": project.requested_chapters,
        "min_words_per_chapter": project.min_words_per_chapter,
        "max_words_per_chapter": project.max_words_per_chapter,
        "preferred_model": project.preferred_model,
        "notes": project.notes,
    }
    merged.update(payload.model_dump(exclude_unset=True))
    validated = ProjectCreate.model_validate(merged)
    return ProjectUpdate(**validated.model_dump())


@router.get("/health")
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> dict:
    client, default_model = _provider_client(settings, db)
    provider = client.health(default_model)
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "database": "ok",
        "provider": provider.model_dump(),
    }


@router.get("/providers/ollama/status", response_model=ProviderCapabilities)
def ollama_status(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> ProviderCapabilities:
    client, default_model = _provider_client(settings, db)
    return client.health(default_model)


@router.get("/providers/ollama/models")
def ollama_models(db: Session = Depends(get_db), settings: Settings = Depends(get_app_settings)) -> dict:
    client, _ = _provider_client(settings, db)
    try:
        return {"models": client.list_models()}
    except OllamaTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/providers/ollama/config", response_model=ProviderCapabilities)
def update_ollama_config(
    payload: ProviderConfigUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ProviderCapabilities:
    from ..repositories import update_provider_config

    config = update_provider_config(db, settings, payload)
    db.commit()
    client = OllamaClient(
        base_url=config.base_url,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
    return client.health(config.default_model)


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def api_create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
) -> ProjectRead:
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


@router.post("/runs", response_model=GenerationRunRead, status_code=status.HTTP_201_CREATED)
def api_create_run(
    payload: RunCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GenerationRunRead:
    project = get_project(db, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    client, default_model = _provider_client(settings, db)
    model_name = (payload.model_name or project.preferred_model or default_model).strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="A model name is required.")
    try:
        client.ensure_model(model_name)
    except OllamaTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = payload.model_copy(update={"model_name": model_name})
    try:
        run = create_run(db, project, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_event(db, run, "run_queued", {"message": "Run queued for processing."})
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


@router.post("/runs/{run_id}/rerun", response_model=GenerationRunRead, status_code=status.HTTP_201_CREATED)
def api_rerun(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GenerationRunRead:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}:
        raise HTTPException(status_code=409, detail="Wait for the current run to finish before re-queueing it.")

    client, _ = _provider_client(settings, db)
    try:
        client.ensure_model(run.model_name)
    except OllamaTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = RunCreate(
        project_id=run.project_id,
        model_name=run.model_name,
        target_word_count=run.target_word_count,
        requested_chapters=run.requested_chapters,
        min_words_per_chapter=run.min_words_per_chapter,
        max_words_per_chapter=run.max_words_per_chapter,
    )
    new_run = create_run(db, run.project, payload)
    record_event(db, new_run, "run_queued", {"message": "Run re-queued with the same settings."})
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

                terminal = run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}
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
