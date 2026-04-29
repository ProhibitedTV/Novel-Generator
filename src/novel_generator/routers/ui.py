from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..dependencies import get_app_settings, get_db, get_templates
from ..models import ChapterStatus, GenerationRun, Project, RunStatus
from ..repositories import (
    create_project,
    create_run,
    delete_project,
    delete_run,
    delete_terminal_runs_for_project,
    ensure_provider_config,
    get_project,
    get_run,
    list_projects,
    list_recent_runs,
    record_event,
    update_project,
    update_provider_config,
)
from ..schemas import ProjectCreate, ProjectUpdate, ProviderCapabilities, ProviderConfigUpdate, RunCreate
from ..services.ollama import OllamaClient, OllamaError, OllamaTransportError
from ..services.storage import delete_run_artifacts_dir, delete_run_artifacts_dirs
from ..services.state import request_run_cancellation
from ..settings import Settings

router = APIRouter(tags=["ui"])

RUN_STAGES = [
    {"id": "queued", "label": "Queued", "description": "Waiting for the worker to pick up the run."},
    {"id": "outline", "label": "Outline", "description": "Building the chapter outline and structure."},
    {"id": "chapter_plan", "label": "Plan chapters", "description": "Turning the outline into concrete chapter beats."},
    {"id": "chapter_draft", "label": "Draft chapters", "description": "Writing chapter text with the chosen model."},
    {"id": "chapter_summary", "label": "Summarize", "description": "Saving continuity summaries for later chapters."},
    {"id": "export", "label": "Export", "description": "Rendering Markdown and DOCX artifacts."},
    {"id": "completed", "label": "Complete", "description": "Artifacts are ready and the run is done."},
    {"id": "failed", "label": "Failed", "description": "The run stopped because something went wrong."},
    {"id": "canceled", "label": "Canceled", "description": "The run was stopped before it finished."},
]
TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}


def _redirect(path: str, **query: str) -> RedirectResponse:
    url = path
    if query:
        url = f"{path}?{urlencode(query)}"
    return RedirectResponse(url=url, status_code=303)


def _render(request: Request, name: str, context: dict[str, Any], status_code: int = 200):
    templates = get_templates()
    merged = {
        "request": request,
        "message": request.query_params.get("message"),
        **context,
    }
    return templates.TemplateResponse(request=request, name=name, context=merged, status_code=status_code)


def _provider_status(settings: Settings, db: Session) -> tuple[Any, ProviderCapabilities, OllamaClient]:
    config = ensure_provider_config(db, settings)
    db.commit()
    client = OllamaClient(
        base_url=config.base_url,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
    status = client.health(config.default_model)
    return config, status, client


def _provider_preview(settings: Settings, base_url: str, default_model: str) -> tuple[ProviderCapabilities, OllamaClient]:
    client = OllamaClient(
        base_url=base_url,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
    status = client.health(default_model)
    return status, client


def _field_errors(exc: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for error in exc.errors():
        loc = error.get("loc", ())
        key = loc[-1] if loc else "__all__"
        if isinstance(key, int):
            key = "__all__"
        errors[str(key)] = error.get("msg", "Invalid value.")
    return errors


def _project_defaults(default_model: str) -> dict[str, Any]:
    return {
        "title": "",
        "premise": "",
        "desired_word_count": 40000,
        "requested_chapters": 12,
        "min_words_per_chapter": 1200,
        "max_words_per_chapter": 2200,
        "preferred_model": default_model,
        "notes": "",
    }


def _project_form_values(default_model: str, project: Project | None = None, values: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _project_defaults(default_model)
    if project is not None:
        base.update(
            {
                "title": project.title,
                "premise": project.premise,
                "desired_word_count": project.desired_word_count,
                "requested_chapters": project.requested_chapters,
                "min_words_per_chapter": project.min_words_per_chapter,
                "max_words_per_chapter": project.max_words_per_chapter,
                "preferred_model": project.preferred_model,
                "notes": project.notes or "",
            }
        )
    if values:
        base.update(values)
    return base


def _run_form_values(project: Project, values: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "model_name": project.preferred_model,
        "target_word_count": project.desired_word_count,
        "requested_chapters": project.requested_chapters,
        "min_words_per_chapter": project.min_words_per_chapter,
        "max_words_per_chapter": project.max_words_per_chapter,
    }
    if values:
        base.update(values)
    return base


def _provider_form_values(base_url: str, default_model: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {"base_url": base_url, "default_model": default_model}
    if values:
        base.update(values)
    return base


def _default_model_verified(default_model: str, provider_status: ProviderCapabilities) -> bool:
    if not default_model.strip():
        return False
    return provider_status.reachable and default_model in provider_status.available_models


def _provider_guidance(
    base_url: str,
    default_model: str,
    provider_status: ProviderCapabilities,
    project_count: int = 0,
) -> dict[str, str] | None:
    if not provider_status.reachable:
        return {
            "tone": "error",
            "title": "Connect Ollama before you queue work.",
            "body": (
                f"Novel Generator cannot reach Ollama at {base_url}. "
                "Open provider settings, confirm the base URL, and use Test connection after saving."
            ),
            "action_href": "/settings/provider",
            "action_label": "Open provider settings",
        }
    if not provider_status.available_models:
        return {
            "tone": "warning",
            "title": "Ollama is reachable, but no models are ready yet.",
            "body": (
                "Pull at least one model in Ollama, then use Refresh models on the provider settings page. "
                "Once a model appears here, project and run forms become verified pickers instead of guesswork."
            ),
            "action_href": "/settings/provider",
            "action_label": "Refresh provider models",
        }
    if default_model not in provider_status.available_models:
        return {
            "tone": "warning",
            "title": "Choose a verified default model.",
            "body": (
                f"The saved default model '{default_model}' is not in Ollama's detected model list. "
                "Pick one of the verified models on the provider settings page to make first-run setup smoother."
            ),
            "action_href": "/settings/provider",
            "action_label": "Choose a verified model",
        }
    if project_count == 0:
        return {
            "tone": "success",
            "title": "Your provider is ready.",
            "body": "The next step is creating your first reusable project so you can queue a run with confidence.",
            "action_href": "/projects/new",
            "action_label": "Create your first project",
        }
    return None


def _onboarding_steps(provider_config: Any, provider_status: ProviderCapabilities, projects: list[Project]) -> list[dict[str, Any]]:
    return [
        {
            "title": "Connect Ollama",
            "description": "Save a working Ollama base URL so the app can talk to your local or remote model host.",
            "done": provider_status.reachable,
            "action_href": "/settings/provider",
            "action_label": "Open provider settings",
        },
        {
            "title": "Detect at least one model",
            "description": "Novel Generator reads Ollama's installed model list and uses it to power verified pickers.",
            "done": bool(provider_status.available_models),
            "action_href": "/settings/provider",
            "action_label": "Refresh models",
        },
        {
            "title": "Choose a verified default model",
            "description": "Pick a model from the detected list so new projects start from something known-good.",
            "done": _default_model_verified(provider_config.default_model, provider_status),
            "action_href": "/settings/provider",
            "action_label": "Choose default model",
        },
        {
            "title": "Create your first project",
            "description": "Projects save the premise, chapter targets, and preferred model so future runs are quicker to launch.",
            "done": bool(projects),
            "action_href": "/projects/new",
            "action_label": "Create project",
        },
    ]


def _sort_runs(project: Project) -> list[GenerationRun]:
    return sorted(project.runs, key=lambda item: item.created_at, reverse=True)


def _chapter_completion_count(run: GenerationRun) -> int:
    return len(
        [
            chapter
            for chapter in run.chapters
            if chapter.status == ChapterStatus.COMPLETED and chapter.content and chapter.summary
        ]
    )


def _project_run_stats(project: Project) -> dict[str, Any]:
    project_runs = _sort_runs(project)
    terminal_runs = [run for run in project_runs if run.status in TERMINAL_STATUSES]
    active_runs = [run for run in project_runs if run.status not in TERMINAL_STATUSES]
    return {
        "project_runs": project_runs,
        "terminal_run_count": len(terminal_runs),
        "active_run_count": len(active_runs),
        "can_delete_project": not active_runs,
    }


def _home_context(request: Request, db: Session, settings: Settings) -> dict[str, Any]:
    provider_config, provider_status, _ = _provider_status(settings, db)
    projects = list_projects(db)
    recent_runs = list_recent_runs(db, limit=6)
    ready_for_projects = provider_status.reachable and bool(provider_status.available_models)
    primary_action = (
        {"href": "/projects/new", "label": "Create a project"}
        if ready_for_projects
        else {"href": "/settings/provider", "label": "Finish provider setup"}
    )
    return {
        "active_nav": "dashboard",
        "provider_config": provider_config,
        "provider_status": provider_status,
        "provider_guidance": _provider_guidance(
            provider_config.base_url,
            provider_config.default_model,
            provider_status,
            len(projects),
        ),
        "onboarding_steps": _onboarding_steps(provider_config, provider_status, projects),
        "projects": projects,
        "recent_projects": projects[:3],
        "recent_runs": recent_runs,
        "has_projects": bool(projects),
        "primary_action": primary_action,
        "ready_for_projects": ready_for_projects,
    }


def _projects_context(request: Request, db: Session, settings: Settings) -> dict[str, Any]:
    provider_config, provider_status, _ = _provider_status(settings, db)
    projects = list_projects(db)
    return {
        "active_nav": "projects",
        "projects": projects,
        "provider_status": provider_status,
        "provider_config": provider_config,
        "provider_guidance": _provider_guidance(provider_config.base_url, provider_config.default_model, provider_status, len(projects)),
    }


def _project_new_context(
    request: Request,
    db: Session,
    settings: Settings,
    form_values: dict[str, Any] | None = None,
    form_errors: dict[str, str] | None = None,
    page_error: str | None = None,
) -> dict[str, Any]:
    provider_config, provider_status, _ = _provider_status(settings, db)
    return {
        "active_nav": "projects",
        "provider_config": provider_config,
        "provider_status": provider_status,
        "provider_guidance": _provider_guidance(provider_config.base_url, provider_config.default_model, provider_status),
        "form_values": _project_form_values(provider_config.default_model, values=form_values),
        "form_errors": form_errors or {},
        "page_error": page_error,
    }


def _project_detail_context(
    request: Request,
    project: Project,
    db: Session,
    settings: Settings,
    edit_values: dict[str, Any] | None = None,
    edit_errors: dict[str, str] | None = None,
    run_values: dict[str, Any] | None = None,
    run_errors: dict[str, str] | None = None,
    page_error: str | None = None,
    open_edit_form: bool = False,
) -> dict[str, Any]:
    provider_config, provider_status, _ = _provider_status(settings, db)
    run_stats = _project_run_stats(project)
    return {
        "active_nav": "projects",
        "project": project,
        **run_stats,
        "provider_config": provider_config,
        "provider_status": provider_status,
        "provider_guidance": _provider_guidance(provider_config.base_url, provider_config.default_model, provider_status, 1),
        "edit_values": _project_form_values(provider_config.default_model, project=project, values=edit_values),
        "edit_errors": edit_errors or {},
        "run_values": _run_form_values(project, values=run_values),
        "run_errors": run_errors or {},
        "page_error": page_error,
        "open_edit_form": open_edit_form,
        "terminal_statuses": TERMINAL_STATUSES,
    }


def _provider_settings_context(
    request: Request,
    db: Session,
    settings: Settings,
    form_values: dict[str, Any] | None = None,
    form_errors: dict[str, str] | None = None,
    page_error: str | None = None,
    provider_status: ProviderCapabilities | None = None,
) -> dict[str, Any]:
    provider_config, saved_status, _ = _provider_status(settings, db)
    values = _provider_form_values(provider_config.base_url, provider_config.default_model, form_values)
    return {
        "active_nav": "provider",
        "provider_config": provider_config,
        "provider_status": provider_status or saved_status,
        "provider_guidance": _provider_guidance(
            values["base_url"],
            values["default_model"],
            provider_status or saved_status,
            len(list_projects(db)),
        ),
        "form_values": values,
        "form_errors": form_errors or {},
        "page_error": page_error,
    }


@router.get("/")
def index(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    return _render(request, "index.html", _home_context(request, db, settings))


@router.get("/projects")
def projects_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    return _render(request, "projects.html", _projects_context(request, db, settings))


@router.get("/projects/new")
def new_project_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    return _render(request, "project_new.html", _project_new_context(request, db, settings))


@router.post("/projects/new")
def create_project_ui(
    request: Request,
    title: str = Form(""),
    premise: str = Form(""),
    desired_word_count: str = Form("40000"),
    requested_chapters: str = Form("12"),
    min_words_per_chapter: str = Form("1200"),
    max_words_per_chapter: str = Form("2200"),
    preferred_model: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    raw_values = {
        "title": title,
        "premise": premise,
        "desired_word_count": desired_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "preferred_model": preferred_model,
        "notes": notes,
    }
    provider_config, provider_status, _ = _provider_status(settings, db)
    try:
        payload = ProjectCreate.model_validate(raw_values)
    except ValidationError as exc:
        return _render(
            request,
            "project_new.html",
            _project_new_context(request, db, settings, form_values=raw_values, form_errors=_field_errors(exc)),
            status_code=400,
        )

    errors: dict[str, str] = {}
    if provider_status.reachable and provider_status.available_models and payload.preferred_model not in provider_status.available_models:
        errors["preferred_model"] = "Choose one of the detected models or fix the provider settings."
    if errors:
        return _render(
            request,
            "project_new.html",
            _project_new_context(request, db, settings, form_values=raw_values, form_errors=errors),
            status_code=400,
        )

    payload = payload.model_copy(update={"notes": payload.notes or None})
    project = create_project(db, payload)
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Project created.")


@router.get("/settings/provider")
def provider_settings_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    return _render(request, "provider_settings.html", _provider_settings_context(request, db, settings))


@router.post("/settings/providers/ollama")
def update_provider_ui(
    request: Request,
    base_url: str = Form(""),
    default_model: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    raw_values = {"base_url": base_url, "default_model": default_model}
    try:
        payload = ProviderConfigUpdate.model_validate(raw_values)
    except ValidationError as exc:
        return _render(
            request,
            "provider_settings.html",
            _provider_settings_context(request, db, settings, form_values=raw_values, form_errors=_field_errors(exc)),
            status_code=400,
        )

    preview_status, _ = _provider_preview(settings, payload.base_url, payload.default_model)
    errors: dict[str, str] = {}
    if preview_status.reachable and preview_status.available_models and payload.default_model not in preview_status.available_models:
        errors["default_model"] = "Choose a default model from the detected list so new projects start from a verified option."
    if errors:
        return _render(
            request,
            "provider_settings.html",
            _provider_settings_context(
                request,
                db,
                settings,
                form_values=raw_values,
                form_errors=errors,
                page_error="These changes were tested but not saved yet.",
                provider_status=preview_status,
            ),
            status_code=400,
        )

    update_provider_config(db, settings, payload)
    db.commit()
    if preview_status.reachable:
        return _redirect("/settings/provider", message="Provider settings saved.")
    return _redirect(
        "/settings/provider",
        message="Provider settings saved. Novel Generator still cannot reach Ollama at that address yet.",
    )


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
    return _render(request, "project_detail.html", _project_detail_context(request, project, db, settings))


@router.post("/projects/{project_id}/edit")
def edit_project_ui(
    project_id: str,
    request: Request,
    title: str = Form(""),
    premise: str = Form(""),
    desired_word_count: str = Form(""),
    requested_chapters: str = Form(""),
    min_words_per_chapter: str = Form(""),
    max_words_per_chapter: str = Form(""),
    preferred_model: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    raw_values = {
        "title": title,
        "premise": premise,
        "desired_word_count": desired_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "preferred_model": preferred_model,
        "notes": notes,
    }
    _, provider_status, _ = _provider_status(settings, db)
    try:
        validated = ProjectCreate.model_validate(raw_values)
    except ValidationError as exc:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                edit_values=raw_values,
                edit_errors=_field_errors(exc),
                open_edit_form=True,
            ),
            status_code=400,
        )

    errors: dict[str, str] = {}
    if provider_status.reachable and provider_status.available_models and validated.preferred_model not in provider_status.available_models:
        errors["preferred_model"] = "Choose one of the detected models or update the provider settings."
    if errors:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                edit_values=raw_values,
                edit_errors=errors,
                open_edit_form=True,
            ),
            status_code=400,
        )

    payload = ProjectUpdate(**validated.model_dump())
    update_project(db, project, payload)
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Project defaults updated.")


@router.post("/projects/{project_id}/delete")
def delete_project_ui(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if any(run.status not in TERMINAL_STATUSES for run in project.runs):
        return _redirect(
            f"/projects/{project.id}",
            message="Cancel or finish active runs before deleting this project.",
        )

    run_ids = delete_project(db, project)
    db.commit()
    delete_run_artifacts_dirs(settings.artifacts_dir, run_ids)
    return _redirect("/projects", message="Project deleted.")


@router.post("/projects/{project_id}/runs/cleanup")
def cleanup_project_runs_ui(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    deleted_run_ids = delete_terminal_runs_for_project(db, project)
    db.commit()
    delete_run_artifacts_dirs(settings.artifacts_dir, deleted_run_ids)
    if not deleted_run_ids:
        return _redirect(f"/projects/{project_id}", message="No finished runs were available to delete.")
    return _redirect(f"/projects/{project_id}", message=f"Deleted {len(deleted_run_ids)} finished run(s).")


@router.post("/projects/{project_id}/runs/new")
def create_run_ui(
    project_id: str,
    request: Request,
    model_name: str = Form(""),
    target_word_count: str = Form(""),
    requested_chapters: str = Form(""),
    min_words_per_chapter: str = Form(""),
    max_words_per_chapter: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    raw_values = {
        "model_name": model_name,
        "target_word_count": target_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
    }
    _, provider_status, client = _provider_status(settings, db)

    if not provider_status.reachable:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                run_values=raw_values,
                run_errors={"__all__": "Novel Generator cannot reach Ollama right now. Fix the provider settings, then queue the run."},
            ),
            status_code=400,
        )
    if not provider_status.available_models:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                run_values=raw_values,
                run_errors={"__all__": "Ollama is reachable but no models are installed yet. Pull a model, refresh provider settings, and try again."},
            ),
            status_code=400,
        )

    try:
        payload = RunCreate.model_validate({"project_id": project_id, **raw_values})
    except ValidationError as exc:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                run_values=raw_values,
                run_errors=_field_errors(exc),
            ),
            status_code=400,
        )

    chosen_model = (payload.model_name or project.preferred_model or provider_status.default_model).strip()
    errors: dict[str, str] = {}
    if chosen_model not in provider_status.available_models:
        errors["model_name"] = "Choose one of the detected models or update the provider settings first."
    if errors:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(request, project, db, settings, run_values=raw_values, run_errors=errors),
            status_code=400,
        )

    payload = payload.model_copy(update={"model_name": chosen_model})
    try:
        client.ensure_model(chosen_model)
        run = create_run(db, project, payload)
    except (OllamaError, OllamaTransportError, ValueError) as exc:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                run_values=raw_values,
                run_errors={"__all__": str(exc)},
            ),
            status_code=400,
        )
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
    completed_chapter_count = _chapter_completion_count(run)
    return _render(
        request,
        "run_detail.html",
        {
            "active_nav": "projects",
            "run": run,
            "project": run.project,
            "show_rerun": run.status in TERMINAL_STATUSES,
            "terminal_statuses": TERMINAL_STATUSES,
            "run_stages": RUN_STAGES,
            "completed_chapter_count": completed_chapter_count,
            "all_requested_chapters_completed": completed_chapter_count == run.requested_chapters,
        },
    )


@router.post("/runs/{run_id}/rerun")
def rerun_ui(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status not in TERMINAL_STATUSES:
        return _redirect(f"/runs/{run.id}", message="Wait for the current run to finish before re-queueing it.")

    _, _, client = _provider_status(settings, db)
    try:
        client.ensure_model(run.model_name)
    except (OllamaError, OllamaTransportError) as exc:
        return _redirect(f"/runs/{run.id}", message=str(exc))

    payload = RunCreate(
        project_id=run.project_id,
        model_name=run.model_name,
        target_word_count=run.target_word_count,
        requested_chapters=run.requested_chapters,
        min_words_per_chapter=run.min_words_per_chapter,
        max_words_per_chapter=run.max_words_per_chapter,
    )
    try:
        new_run = create_run(db, run.project, payload)
    except ValueError as exc:
        return _redirect(f"/runs/{run.id}", message=str(exc))
    record_event(db, new_run, "run_queued", {"message": "Run re-queued with the same settings."})
    db.commit()
    return _redirect(f"/runs/{new_run.id}", message="Run re-queued.")


@router.post("/runs/{run_id}/delete")
def delete_run_ui(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status not in TERMINAL_STATUSES:
        return _redirect(f"/runs/{run.id}", message="Only finished runs can be deleted.")

    project_id = run.project_id
    deleted_run_id = delete_run(db, run)
    db.commit()
    delete_run_artifacts_dir(settings.artifacts_dir, deleted_run_id)
    return _redirect(f"/projects/{project_id}", message="Run deleted.")


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
    if run.status not in TERMINAL_STATUSES:
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
