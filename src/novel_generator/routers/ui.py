from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..dependencies import get_app_settings, get_db, get_templates
from ..models import RunStatus
from ..repositories import (
    create_project,
    create_run,
    ensure_provider_config,
    get_project,
    get_run,
    list_projects,
    record_event,
    update_provider_config,
)
from ..schemas import ProjectCreate, ProviderConfigUpdate, RunCreate
from ..services.ollama import OllamaClient, OllamaError, OllamaTransportError
from ..services.state import request_run_cancellation
from ..settings import Settings

router = APIRouter(tags=["ui"])


def _redirect(path: str, **query: str) -> RedirectResponse:
    url = path
    if query:
        url = f"{path}?{urlencode(query)}"
    return RedirectResponse(url=url, status_code=303)


def _provider_status(settings: Settings, db: Session):
    config = ensure_provider_config(db, settings)
    db.commit()
    client = OllamaClient(
        base_url=config.base_url,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
    status = client.health(config.default_model)
    return config, status, client


@router.get("/")
def index(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    templates = get_templates()
    config, provider_status, _ = _provider_status(settings, db)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "projects": list_projects(db),
            "provider_config": config,
            "provider_status": provider_status,
            "message": request.query_params.get("message"),
        },
    )


@router.post("/projects/new")
def create_project_ui(
    title: str = Form(...),
    premise: str = Form(...),
    desired_word_count: int = Form(...),
    requested_chapters: int = Form(...),
    min_words_per_chapter: int = Form(...),
    max_words_per_chapter: int = Form(...),
    preferred_model: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    payload = ProjectCreate(
        title=title,
        premise=premise,
        desired_word_count=desired_word_count,
        requested_chapters=requested_chapters,
        min_words_per_chapter=min_words_per_chapter,
        max_words_per_chapter=max_words_per_chapter,
        preferred_model=preferred_model,
        notes=notes or None,
    )
    project = create_project(db, payload)
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Project created.")


@router.post("/settings/providers/ollama")
def update_provider_ui(
    base_url: str = Form(...),
    default_model: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    payload = ProviderConfigUpdate(base_url=base_url, default_model=default_model)
    update_provider_config(db, settings, payload)
    db.commit()
    return _redirect("/", message="Ollama settings updated.")


@router.get("/projects/{project_id}")
def project_detail(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    templates = get_templates()
    config, provider_status, _ = _provider_status(settings, db)
    return templates.TemplateResponse(
        "project_detail.html",
        {
            "request": request,
            "project": project,
            "provider_config": config,
            "provider_status": provider_status,
            "message": request.query_params.get("message"),
        },
    )


@router.post("/projects/{project_id}/runs/new")
def create_run_ui(
    project_id: str,
    model_name: str = Form(""),
    target_word_count: int = Form(...),
    requested_chapters: int = Form(...),
    min_words_per_chapter: int = Form(...),
    max_words_per_chapter: int = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    _, provider_status, client = _provider_status(settings, db)
    chosen_model = (model_name or project.preferred_model or provider_status.default_model).strip()
    try:
        client.ensure_model(chosen_model)
    except (OllamaError, OllamaTransportError) as exc:
        return _redirect(f"/projects/{project_id}", message=str(exc))

    payload = RunCreate(
        project_id=project_id,
        model_name=chosen_model,
        target_word_count=target_word_count,
        requested_chapters=requested_chapters,
        min_words_per_chapter=min_words_per_chapter,
        max_words_per_chapter=max_words_per_chapter,
    )
    try:
        run = create_run(db, project, payload)
    except ValueError as exc:
        return _redirect(f"/projects/{project_id}", message=str(exc))
    record_event(db, run, "run_queued", {"message": "Run queued from the web UI."})
    db.commit()
    return _redirect(f"/runs/{run.id}", message="Run queued.")


@router.get("/runs/{run_id}")
def run_detail(
    run_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    templates = get_templates()
    return templates.TemplateResponse(
        "run_detail.html",
        {
            "request": request,
            "run": run,
            "project": run.project,
            "message": request.query_params.get("message"),
            "terminal_statuses": {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED},
        },
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run_ui(run_id: str, db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    request_run_cancellation(db, run)
    db.commit()
    return _redirect(f"/runs/{run.id}", message="Cancellation requested.")


@router.post("/runs/{run_id}/chapters/{chapter_number}/regenerate")
def regenerate_chapter_ui(
    run_id: str,
    chapter_number: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}:
        return _redirect(f"/runs/{run_id}", message="Wait for the current run to finish before regenerating.")
    _, _, client = _provider_status(settings, db)
    try:
        client.ensure_model(run.model_name)
    except (OllamaError, OllamaTransportError) as exc:
        return _redirect(f"/runs/{run_id}", message=str(exc))
    payload = RunCreate(
        project_id=run.project_id,
        model_name=run.model_name,
        target_word_count=run.target_word_count,
        requested_chapters=run.requested_chapters,
        min_words_per_chapter=run.min_words_per_chapter,
        max_words_per_chapter=run.max_words_per_chapter,
        source_run_id=run.id,
        resume_from_chapter=chapter_number,
    )
    try:
        new_run = create_run(db, run.project, payload)
    except ValueError as exc:
        return _redirect(f"/runs/{run_id}", message=str(exc))
    record_event(
        db,
        new_run,
        "run_queued",
        {
            "message": f"Queued regeneration from chapter {chapter_number}.",
            "resume_from_chapter": chapter_number,
        },
    )
    db.commit()
    return _redirect(f"/runs/{new_run.id}", message=f"Queued regeneration from chapter {chapter_number}.")
