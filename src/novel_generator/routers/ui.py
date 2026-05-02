from __future__ import annotations

import json
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
    ensure_provider_configs,
    get_project,
    get_run,
    list_projects,
    list_recent_runs,
    record_event,
    update_project,
    update_provider_config,
)
from ..schemas import ProjectCreate, ProjectUpdate, ProviderCapabilities, ProviderConfigUpdate, RunCreate
from ..services.openai_compatible import OpenAICompatibleClient
from ..services.ollama import OllamaClient
from ..services.provider_errors import ProviderError, ProviderTransportError
from ..services.providers import ProviderManager, TASK_ROUTE_STAGES, provider_definition, provider_options
from ..services.state import approve_outline_review, request_run_cancellation
from ..services.storage import delete_run_artifacts_dir, delete_run_artifacts_dirs
from ..settings import Settings

router = APIRouter(tags=["ui"])

RUN_STAGES = [
    {
        "id": "queued",
        "label": "Queued",
        "description": "Waiting for the worker to pick up the run.",
        "why": "This usually means another run is active or the worker has not started the next long generation step yet.",
        "result": "No manuscript text is being generated yet, but the run settings and routing snapshot are locked in.",
    },
    {
        "id": "story_bible",
        "label": "Story bible",
        "description": "Building the book's core promise, cast, and system rules.",
        "why": "The model is turning the brief into canon, ideology, pacing rules, and continuity anchors for the whole manuscript.",
        "result": "A structured story bible should appear here with the logline, cast, canon registry, and conflict ladder.",
    },
    {
        "id": "outline",
        "label": "Outline",
        "description": "Generating the chapter-by-chapter structure and ending path.",
        "why": "This stage lays down the chapter contract for the whole run, so it can take time to balance pacing, reversals, and ending promises.",
        "result": "Each chapter should gain an objective, obstacle, cost, character turn, and concrete ending hook.",
    },
    {
        "id": "outline_review",
        "label": "Review outline",
        "description": "Paused so you can approve the structure before the long draft begins.",
        "why": "The worker is intentionally stopped here so you can catch structural problems before spending hours on full chapter drafting.",
        "result": "Approve the outline to continue, or cancel and edit the project if the structure is off.",
    },
    {
        "id": "chapter_plan",
        "label": "Plan chapter",
        "description": "Turning the approved outline into concrete scene beats.",
        "why": "The system is defining the specific attempt, complication, interpersonal conflict, and ending delivery for the active chapter.",
        "result": "The current chapter contract should fill in with scene-level beats and costs.",
    },
    {
        "id": "chapter_draft",
        "label": "Draft chapter",
        "description": "Writing prose for the current chapter.",
        "why": "This is usually the longest stage because the model is producing full prose while obeying the story bible, canon, pacing rules, and latest continuity ledger.",
        "result": "Word count should climb and the chapter preview will become available after the draft is saved.",
    },
    {
        "id": "chapter_revision",
        "label": "Revise chapter",
        "description": "Running one editorial cleanup pass when the critique says it needs it.",
        "why": "The worker only spends extra time here when the draft needs a targeted repair for endings, continuity, emotional depth, or low-cost solutions.",
        "result": "Quality signals and revision triggers should explain what the repair pass is trying to fix.",
    },
    {
        "id": "chapter_summary",
        "label": "Update continuity",
        "description": "Saving the chapter summary and run-level continuity ledger.",
        "why": "The system is freezing what changed so later chapters do not drift on canon, open promises, emotional fallout, or world state.",
        "result": "Continuity highlights should update with the latest chapter outcome, world state, and named-entity changes.",
    },
    {
        "id": "manuscript_qa",
        "label": "Editorial QA",
        "description": "Checking the full manuscript for repetition, continuity drift, and ending shape.",
        "why": "This stage reads the manuscript as a whole and merges model critique with deterministic lint findings.",
        "result": "A QA report artifact should appear with strengths, warnings, and structural risks.",
    },
    {
        "id": "export",
        "label": "Export",
        "description": "Rendering manuscript and QA artifacts.",
        "why": "The worker is packaging the final manuscript files and editorial report so they can be downloaded later.",
        "result": "Markdown, DOCX, and QA artifacts should attach to the run.",
    },
    {
        "id": "completed",
        "label": "Complete",
        "description": "Artifacts are ready and the run is done.",
        "why": "All requested chapters, continuity checkpoints, QA passes, and exports are finished.",
        "result": "Use the artifacts and chapter QA to decide whether to rerun, regenerate from a chapter, or revise manually.",
    },
    {
        "id": "failed",
        "label": "Failed",
        "description": "The run stopped because something went wrong.",
        "why": "A provider error, parse failure, validation problem, or unrecoverable pipeline issue interrupted the run.",
        "result": "Check the latest events and error message to see where it failed before rerunning.",
    },
    {
        "id": "canceled",
        "label": "Canceled",
        "description": "The run was stopped before it finished.",
        "why": "Cancellation is usually used when the outline or early chapter signals say the run is not worth finishing.",
        "result": "You can rerun with the same settings or edit the project before trying again.",
    },
]
TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}
RUN_STAGE_LOOKUP = {stage["id"]: stage for stage in RUN_STAGES}
RUN_STAGE_PROGRESS_ORDER = [
    "queued",
    "story_bible",
    "outline",
    "outline_review",
    "chapter_plan",
    "chapter_draft",
    "chapter_revision",
    "chapter_summary",
    "manuscript_qa",
    "export",
    "completed",
]
QUALITY_SIGNAL_DEFS = [
    {"field": "forward_motion_score", "label": "Forward motion", "lower_is_better": False},
    {"field": "ending_concreteness_score", "label": "Ending concreteness", "lower_is_better": False},
    {"field": "cost_consequence_realism_score", "label": "Cost realism", "lower_is_better": False},
    {"field": "emotional_depth_score", "label": "Emotional depth", "lower_is_better": False},
    {"field": "side_character_independence_score", "label": "Side-character agency", "lower_is_better": False},
    {"field": "proper_noun_continuity_score", "label": "Proper-noun continuity", "lower_is_better": False},
    {"field": "ideology_clarity_score", "label": "Ideology clarity", "lower_is_better": False},
    {"field": "civilian_texture_score", "label": "Civilian texture", "lower_is_better": False},
    {"field": "repetition_risk_score", "label": "Repetition risk", "lower_is_better": True},
]


def _score_state(score: int, *, lower_is_better: bool) -> tuple[str, str]:
    if lower_is_better:
        if score <= 3:
            return "healthy", "Low"
        if score <= 5:
            return "watch", "Watch"
        return "risk", "High"
    if score >= 8:
        return "healthy", "Strong"
    if score >= 6:
        return "steady", "Healthy"
    if score >= 4:
        return "watch", "Watch"
    return "risk", "Weak"


def _sorted_run_events(run: GenerationRun) -> list[Any]:
    return sorted(run.events, key=lambda item: item.sequence)


def _sorted_run_chapters(run: GenerationRun) -> list[Any]:
    return sorted(run.chapters, key=lambda item: item.chapter_number)


def _normalize_run_stage(run: GenerationRun) -> str:
    if run.status == RunStatus.AWAITING_APPROVAL:
        return "outline_review"
    if run.status == RunStatus.COMPLETED:
        return "completed"
    if run.status == RunStatus.FAILED:
        return "failed"
    if run.status == RunStatus.CANCELED:
        return "canceled"
    if run.current_step in {"", "starting", "recovered", None}:
        return "queued"
    return run.current_step


def _stage_context(run: GenerationRun) -> tuple[dict[str, Any], dict[str, Any] | None]:
    stage_id = _normalize_run_stage(run)
    current_stage = RUN_STAGE_LOOKUP.get(stage_id, RUN_STAGE_LOOKUP["queued"])
    next_stage: dict[str, Any] | None = None
    if stage_id in RUN_STAGE_PROGRESS_ORDER:
        index = RUN_STAGE_PROGRESS_ORDER.index(stage_id)
        if index + 1 < len(RUN_STAGE_PROGRESS_ORDER):
            next_stage = RUN_STAGE_LOOKUP[RUN_STAGE_PROGRESS_ORDER[index + 1]]
    return current_stage, next_stage


def _safe_plan(chapter: Any | None) -> dict[str, Any]:
    if chapter is None or not chapter.plan:
        return {}
    try:
        parsed = json.loads(chapter.plan)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_route_context(run: GenerationRun) -> dict[str, str]:
    for event in reversed(_sorted_run_events(run)):
        payload = event.payload or {}
        provider_name = str(payload.get("provider_name", "") or "").strip()
        model_name = str(payload.get("model_name", "") or "").strip()
        if provider_name or model_name:
            return {
                "provider_name": provider_name or run.provider_name,
                "model_name": model_name or run.model_name,
            }
    return {"provider_name": run.provider_name, "model_name": run.model_name}


def _chapter_for_dashboard(run: GenerationRun) -> Any | None:
    chapters = _sorted_run_chapters(run)
    if not chapters:
        return None
    if run.current_chapter:
        for chapter in chapters:
            if chapter.chapter_number == run.current_chapter:
                return chapter
    for chapter in chapters:
        if chapter.status != ChapterStatus.COMPLETED:
            return chapter
    return chapters[-1]


def _latest_quality_chapter(run: GenerationRun, preferred: Any | None = None) -> Any | None:
    if preferred is not None and preferred.qa_notes:
        return preferred
    for chapter in reversed(_sorted_run_chapters(run)):
        if chapter.qa_notes:
            return chapter
    return preferred


def _latest_continuity_chapter(run: GenerationRun, preferred: Any | None = None) -> Any | None:
    if preferred is not None and preferred.continuity_update:
        return preferred
    for chapter in reversed(_sorted_run_chapters(run)):
        if chapter.continuity_update:
            return chapter
    return preferred


def _outline_entry(run: GenerationRun, chapter_number: int | None) -> dict[str, Any] | None:
    if chapter_number is None:
        return None
    for item in list(run.outline or []):
        if int(item.get("chapter_number", 0) or 0) == chapter_number:
            return item
    return None


def _current_contract_context(run: GenerationRun) -> tuple[dict[str, Any] | None, Any | None]:
    chapter = _chapter_for_dashboard(run)
    if chapter is None:
        return None, None
    outline = _outline_entry(run, chapter.chapter_number) or {}
    ending_hook = outline.get("concrete_ending_hook") or {}
    plan = _safe_plan(chapter)
    contract = {
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "status": chapter.status.value,
        "objective": outline.get("objective", chapter.outline_summary),
        "primary_obstacle": outline.get("primary_obstacle", ""),
        "character_turn": outline.get("character_turn", ""),
        "cost_if_success": outline.get("cost_if_success", ""),
        "side_character_friction": outline.get("side_character_friction", ""),
        "chapter_mode": outline.get("chapter_mode", ""),
        "civilian_life_detail": outline.get("civilian_life_detail", ""),
        "emotional_reveal": outline.get("emotional_reveal", ""),
        "ideology_pressure": outline.get("ideology_pressure", ""),
        "ending_trigger": ending_hook.get("trigger", ""),
        "ending_visible_actor": ending_hook.get("visible_object_or_actor", ""),
        "ending_next_problem": ending_hook.get("next_problem", ""),
        "attempt": plan.get("attempt", ""),
        "complication": plan.get("complication", ""),
        "price_paid": plan.get("price_paid", ""),
        "partial_failure_mode": plan.get("partial_failure_mode", ""),
        "ending_hook_delivery": plan.get("ending_hook_delivery", ""),
        "emotional_anchor": plan.get("emotional_anchor", ""),
        "civilian_texture": plan.get("civilian_texture", ""),
        "ideology_clash": plan.get("ideology_clash", ""),
        "primary_interpersonal_conflict": plan.get("primary_interpersonal_conflict", ""),
    }
    return contract, chapter


def _quality_signal_rows(chapter: Any | None) -> list[dict[str, Any]]:
    if chapter is None or not chapter.qa_notes:
        return []
    qa_notes = chapter.qa_notes or {}
    rows: list[dict[str, Any]] = []
    for item in QUALITY_SIGNAL_DEFS:
        score = int(qa_notes.get(item["field"], 0) or 0)
        tone, state_label = _score_state(score, lower_is_better=bool(item["lower_is_better"]))
        rows.append(
            {
                "label": item["label"],
                "score": score,
                "tone": tone,
                "state_label": state_label,
                "lower_is_better": bool(item["lower_is_better"]),
            }
        )
    return rows


def _revision_trigger_rows(chapter: Any | None) -> list[dict[str, str]]:
    if chapter is None or not chapter.qa_notes:
        return []
    qa_notes = chapter.qa_notes or {}
    rows: list[dict[str, str]] = []
    repair_scope = str(qa_notes.get("repair_scope", "none") or "none").strip()
    if repair_scope and repair_scope != "none":
        rows.append({"tone": "info", "text": f"Repair scope used: {repair_scope.replace('_', ' ')}."})
    for item in qa_notes.get("blocking_issues", []) or []:
        rows.append({"tone": "error", "text": str(item)})
    for item in qa_notes.get("soft_warnings", []) or []:
        rows.append({"tone": "warning", "text": str(item)})
    for item in qa_notes.get("focus", []) or []:
        rows.append({"tone": "neutral", "text": str(item)})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if row["text"] in seen:
            continue
        seen.add(row["text"])
        deduped.append(row)
    return deduped[:8]


def _continuity_snapshot(run: GenerationRun, chapter: Any | None) -> dict[str, Any] | None:
    source = _latest_continuity_chapter(run, preferred=chapter)
    ledger = run.continuity_ledger or {}
    if source is None and not ledger:
        return None
    update = (source.continuity_update or {}) if source is not None else {}
    entity_changes = [f"{name}: {state}" for name, state in (update.get("entity_state_changes", {}) or {}).items()]
    open_promises = [f"{name}: {state}" for name, state in (update.get("open_promises_by_name", {}) or {}).items()]
    emotional_loops = [f"{name}: {state}" for name, state in (update.get("emotional_open_loops", {}) or {}).items()]
    trust_fractures = [f"{name}: {state}" for name, state in (update.get("trust_fractures", {}) or {}).items()]
    memory_damage = [f"{name}: {state}" for name, state in (update.get("memory_damage", {}) or {}).items()]
    return {
        "chapter_number": source.chapter_number if source is not None else None,
        "chapter_outcome": update.get("chapter_outcome", ""),
        "timeline_entry": update.get("timeline_entry", ""),
        "current_patch_status": update.get("current_patch_status", ledger.get("current_patch_status", "")),
        "world_state": update.get("world_state", ledger.get("world_state", "")),
        "entity_changes": entity_changes,
        "open_promises": open_promises,
        "emotional_loops": emotional_loops,
        "trust_fractures": trust_fractures,
        "memory_damage": memory_damage,
        "civilian_pressure_points": list(update.get("civilian_pressure_points", []) or []),
    }


def _run_word_count(run: GenerationRun) -> int:
    return sum(int(chapter.word_count or 0) for chapter in run.chapters)


def _word_progress_percent(run: GenerationRun) -> int:
    if run.target_word_count <= 0:
        return 0
    return min(100, int(round((_run_word_count(run) / run.target_word_count) * 100)))


def _run_dashboard_context(run: GenerationRun) -> dict[str, Any]:
    current_stage, next_stage = _stage_context(run)
    current_contract, dashboard_chapter = _current_contract_context(run)
    quality_chapter = _latest_quality_chapter(run, preferred=dashboard_chapter)
    quality_source = quality_chapter.chapter_number if quality_chapter is not None and quality_chapter.qa_notes else None
    continuity = _continuity_snapshot(run, dashboard_chapter)
    return {
        "current_stage_context": current_stage,
        "next_stage_context": next_stage,
        "current_route_context": _latest_route_context(run),
        "current_contract": current_contract,
        "quality_signal_rows": _quality_signal_rows(quality_chapter),
        "quality_source_chapter": quality_source,
        "revision_trigger_rows": _revision_trigger_rows(quality_chapter),
        "continuity_snapshot": continuity,
        "total_run_words": _run_word_count(run),
        "word_progress_percent": _word_progress_percent(run),
        "run_stage_data": RUN_STAGES,
        "event_count": len(run.events),
    }


def _redirect(
    path: str,
    *,
    message: str | None = None,
    message_tone: str = "success",
    **query: str,
) -> RedirectResponse:
    url = path
    if message is not None:
        query["message"] = message
        query["message_tone"] = message_tone
    if query:
        url = f"{path}?{urlencode(query)}"
    return RedirectResponse(url=url, status_code=303)


def _render(request: Request, name: str, context: dict[str, Any], status_code: int = 200):
    templates = get_templates()
    merged = {
        "request": request,
        "message": request.query_params.get("message"),
        "message_tone": request.query_params.get("message_tone", "success"),
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


def _provider_preview(
    settings: Settings,
    provider_name: str,
    base_url: str,
    default_model: str,
    *,
    api_key: str | None = None,
    is_enabled: bool = True,
) -> tuple[ProviderCapabilities, Any]:
    if not is_enabled:
        status = ProviderCapabilities(
            provider_name=provider_name,
            reachable=False,
            base_url=base_url.rstrip("/"),
            default_model=default_model.strip(),
            available_models=[],
            error="Provider is disabled.",
        )
        return status, None

    if provider_name == "openai_compatible":
        client = OpenAICompatibleClient(
            base_url=base_url,
            timeout_seconds=settings.ollama_timeout_seconds,
            max_retries=settings.ollama_max_retries,
            api_key=api_key,
        )
    else:
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


def _join_lines(values: list[str] | None) -> str:
    return "\n".join(item for item in (values or []) if str(item).strip())


def _story_brief_form_values(story_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    brief = story_brief or {}
    return {
        "story_setting": str(brief.get("setting", "") or ""),
        "story_tone": str(brief.get("tone", "") or ""),
        "story_protagonist": str(brief.get("protagonist", "") or ""),
        "story_supporting_cast": _join_lines(brief.get("supporting_cast", [])),
        "story_antagonist": str(brief.get("antagonist", "") or ""),
        "story_core_conflict": str(brief.get("core_conflict", "") or ""),
        "story_ending_target": str(brief.get("ending_target", "") or ""),
        "story_world_rules": _join_lines(brief.get("world_rules", [])),
        "story_must_include": _join_lines(brief.get("must_include", [])),
        "story_avoid": _join_lines(brief.get("avoid", [])),
    }


def _story_brief_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "setting": values.get("story_setting", ""),
        "tone": values.get("story_tone", ""),
        "protagonist": values.get("story_protagonist", ""),
        "supporting_cast": values.get("story_supporting_cast", ""),
        "antagonist": values.get("story_antagonist", ""),
        "core_conflict": values.get("story_core_conflict", ""),
        "ending_target": values.get("story_ending_target", ""),
        "world_rules": values.get("story_world_rules", ""),
        "must_include": values.get("story_must_include", ""),
        "avoid": values.get("story_avoid", ""),
    }


def _coerce_checkbox(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() not in {"0", "false", "off", ""}


def _project_defaults(default_model: str) -> dict[str, Any]:
    return {
        "title": "",
        "premise": "",
        "desired_word_count": 40000,
        "requested_chapters": 12,
        "min_words_per_chapter": 1200,
        "max_words_per_chapter": 2200,
        "preferred_provider_name": "ollama",
        "preferred_model": default_model,
        "notes": "",
        **_story_brief_form_values(),
        **_task_routing_form_values(),
    }


def _project_form_values(
    default_provider_name: str,
    default_model: str,
    project: Project | None = None,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = _project_defaults(default_model)
    base["preferred_provider_name"] = default_provider_name
    if project is not None:
        base.update(
            {
                "title": project.title,
                "premise": project.premise,
                "desired_word_count": project.desired_word_count,
                "requested_chapters": project.requested_chapters,
                "min_words_per_chapter": project.min_words_per_chapter,
                "max_words_per_chapter": project.max_words_per_chapter,
                "preferred_provider_name": project.preferred_provider_name,
                "preferred_model": project.preferred_model,
                "notes": project.notes or "",
                **_story_brief_form_values(project.story_brief),
                **_task_routing_form_values(project.task_routing),
            }
        )
    if values:
        base.update(values)
    return base


def _run_form_values(project: Project, values: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "provider_name": project.preferred_provider_name,
        "model_name": project.preferred_model,
        "target_word_count": project.desired_word_count,
        "requested_chapters": project.requested_chapters,
        "min_words_per_chapter": project.min_words_per_chapter,
        "max_words_per_chapter": project.max_words_per_chapter,
        "pause_after_outline": True,
        **_task_routing_form_values(project.task_routing),
    }
    if values:
        base.update(values)
    return base


def _provider_form_values(base_url: str, default_model: str, values: dict[str, Any] | None = None, *, api_key: str = "", is_enabled: bool = True) -> dict[str, Any]:
    base = {"base_url": base_url, "default_model": default_model, "api_key": api_key, "is_enabled": is_enabled}
    if values:
        base.update(values)
    return base


def _task_routing_form_values(task_routing: dict[str, Any] | None = None, values: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {}
    routing = task_routing or {}
    for stage in TASK_ROUTE_STAGES:
        entry = routing.get(stage["id"]) or {}
        base[f"route_{stage['id']}_provider"] = str(entry.get("provider_name", "") or "")
        base[f"route_{stage['id']}_model"] = str(entry.get("model_name", "") or "")
    if values:
        base.update(values)
    return base


def _task_routing_payload(values: dict[str, Any]) -> dict[str, Any]:
    routing: dict[str, Any] = {}
    for stage in TASK_ROUTE_STAGES:
        provider_name = str(values.get(f"route_{stage['id']}_provider", "") or "").strip()
        model_name = str(values.get(f"route_{stage['id']}_model", "") or "").strip()
        if not provider_name and not model_name:
            continue
        routing[stage["id"]] = {"provider_name": provider_name, "model_name": model_name}
    return routing


def _task_route_rows(form_values: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "id": stage["id"],
            "label": stage["label"],
            "provider_field": f"route_{stage['id']}_provider",
            "model_field": f"route_{stage['id']}_model",
            "provider_name": str(form_values.get(f"route_{stage['id']}_provider", "") or ""),
            "model_name": str(form_values.get(f"route_{stage['id']}_model", "") or ""),
        }
        for stage in TASK_ROUTE_STAGES
    ]


def _provider_catalog(settings: Settings, db: Session) -> tuple[dict[str, Any], dict[str, ProviderCapabilities], ProviderManager]:
    configs = ensure_provider_configs(db, settings)
    db.commit()
    manager = ProviderManager(settings, configs)
    configs_by_name = {config.provider_name: config for config in configs}
    statuses_by_name = {config.provider_name: manager.health(config.provider_name) for config in configs}
    return configs_by_name, statuses_by_name, manager


def _provider_option_rows(configs_by_name: dict[str, Any], statuses_by_name: dict[str, ProviderCapabilities]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for option in provider_options():
        config = configs_by_name[option["name"]]
        status = statuses_by_name[option["name"]]
        rows.append(
            {
                **option,
                "is_enabled": config.is_enabled,
                "reachable": status.reachable,
                "available_models": status.available_models,
                "default_model": config.default_model,
            }
        )
    return rows


def _validate_provider_selection(
    provider_name: str,
    model_name: str,
    configs_by_name: dict[str, Any],
    statuses_by_name: dict[str, ProviderCapabilities],
) -> str | None:
    config = configs_by_name.get(provider_name)
    if config is None:
        return "Choose a supported provider."
    if not config.is_enabled:
        return f"Enable {provider_definition(provider_name).label} in provider settings first."
    status = statuses_by_name.get(provider_name)
    if status and status.reachable and status.available_models and model_name not in status.available_models:
        return "Choose one of the detected models or update the provider settings."
    return None


def _validate_task_routing(
    routing: dict[str, Any],
    configs_by_name: dict[str, Any],
    statuses_by_name: dict[str, ProviderCapabilities],
) -> dict[str, str]:
    errors: dict[str, str] = {}
    for stage in TASK_ROUTE_STAGES:
        entry = routing.get(stage["id"]) or {}
        provider_name = str(entry.get("provider_name", "") or "").strip()
        model_name = str(entry.get("model_name", "") or "").strip()
        provider_field = f"route_{stage['id']}_provider"
        model_field = f"route_{stage['id']}_model"
        if not provider_name and not model_name:
            continue
        if not provider_name:
            errors[provider_field] = "Choose a provider for this override."
            continue
        if not model_name:
            errors[model_field] = "Enter a model for this override."
            continue
        message = _validate_provider_selection(provider_name, model_name, configs_by_name, statuses_by_name)
        if message:
            errors[model_field] = message
    return errors


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
            "description": "Projects save the premise, chapter targets, preferred model, and story brief so future runs are quicker to launch.",
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
            if chapter.status == ChapterStatus.COMPLETED
            and chapter.content
            and chapter.summary
            and chapter.continuity_update
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


def _artifact_context(run: GenerationRun) -> dict[str, Any]:
    qa_artifact = next((artifact for artifact in run.artifacts if artifact.kind == "qa-report"), None)
    manuscript_artifacts = [artifact for artifact in run.artifacts if artifact.kind != "qa-report"]
    return {
        "qa_artifact": qa_artifact,
        "manuscript_artifacts": manuscript_artifacts,
    }


def _home_context(request: Request, db: Session, settings: Settings) -> dict[str, Any]:
    provider_config, provider_status, _ = _provider_status(settings, db)
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
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
        "provider_options": _provider_option_rows(provider_configs_by_name, provider_statuses_by_name),
    }


def _projects_context(request: Request, db: Session, settings: Settings) -> dict[str, Any]:
    provider_config, provider_status, _ = _provider_status(settings, db)
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
    projects = list_projects(db)
    return {
        "active_nav": "projects",
        "projects": projects,
        "provider_status": provider_status,
        "provider_config": provider_config,
        "provider_guidance": _provider_guidance(
            provider_config.base_url,
            provider_config.default_model,
            provider_status,
            len(projects),
        ),
        "provider_options": _provider_option_rows(provider_configs_by_name, provider_statuses_by_name),
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
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
    projects = list_projects(db)
    form_payload = _project_form_values(provider_config.provider_name, provider_config.default_model, values=form_values)
    return {
        "active_nav": "projects",
        "provider_config": provider_config,
        "provider_status": provider_status,
        "provider_statuses": provider_statuses_by_name,
        "provider_guidance": _provider_guidance(provider_config.base_url, provider_config.default_model, provider_status),
        "setup_steps": _onboarding_steps(provider_config, provider_status, projects),
        "form_values": form_payload,
        "provider_options": _provider_option_rows(provider_configs_by_name, provider_statuses_by_name),
        "task_route_rows": _task_route_rows(form_payload),
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
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
    run_stats = _project_run_stats(project)
    edit_payload = _project_form_values(
        project.preferred_provider_name or provider_config.provider_name,
        project.preferred_model or provider_config.default_model,
        project=project,
        values=edit_values,
    )
    run_payload = _run_form_values(project, values=run_values)
    return {
        "active_nav": "projects",
        "project": project,
        **run_stats,
        "provider_config": provider_config,
        "provider_status": provider_status,
        "provider_statuses": provider_statuses_by_name,
        "provider_guidance": _provider_guidance(provider_config.base_url, provider_config.default_model, provider_status, 1),
        "edit_values": edit_payload,
        "edit_errors": edit_errors or {},
        "edit_task_route_rows": _task_route_rows(edit_payload),
        "run_values": run_payload,
        "run_errors": run_errors or {},
        "run_task_route_rows": _task_route_rows(run_payload),
        "provider_options": _provider_option_rows(provider_configs_by_name, provider_statuses_by_name),
        "page_error": page_error,
        "open_edit_form": open_edit_form or request.query_params.get("open_edit") == "1",
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
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
    projects = list_projects(db)
    provider_cards = []
    form_errors = form_errors or {}
    for provider_name, config in provider_configs_by_name.items():
        values = _provider_form_values(
            config.base_url,
            config.default_model,
            (form_values or {}).get(provider_name),
            api_key="" if provider_name == "ollama" else "",
            is_enabled=config.is_enabled,
        )
        provider_cards.append(
            {
                "provider_name": provider_name,
                "provider_label": provider_definition(provider_name).label,
                "provider_description": provider_definition(provider_name).description,
                "config": config,
                "status": provider_statuses_by_name.get(provider_name) if provider_status is None or provider_name != provider_config.provider_name else provider_status,
                "form_values": values,
                "form_errors": form_errors.get(provider_name, {}) if isinstance(form_errors.get(provider_name), dict) else {},
                "supports_api_key": provider_definition(provider_name).requires_api_key,
            }
        )
    return {
        "active_nav": "provider",
        "provider_config": provider_config,
        "provider_status": provider_status or saved_status,
        "provider_cards": provider_cards,
        "provider_guidance": _provider_guidance(
            provider_config.base_url,
            provider_config.default_model,
            provider_status or saved_status,
            len(projects),
        ),
        "setup_steps": _onboarding_steps(provider_config, provider_status or saved_status, projects),
        "page_error": page_error,
    }


def _same_settings_payload(run: GenerationRun, *, source_run_id: str | None = None, resume_from_chapter: int | None = None) -> RunCreate:
    return RunCreate(
        project_id=run.project_id,
        provider_name=run.provider_name,
        model_name=run.model_name,
        target_word_count=run.target_word_count,
        requested_chapters=run.requested_chapters,
        min_words_per_chapter=run.min_words_per_chapter,
        max_words_per_chapter=run.max_words_per_chapter,
        pause_after_outline=True,
        task_routing=run.task_routing or {},
        source_run_id=source_run_id,
        resume_from_chapter=resume_from_chapter,
    )


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
    preferred_provider_name: str = Form("ollama"),
    preferred_model: str = Form(""),
    notes: str = Form(""),
    route_story_bible_provider: str = Form(""),
    route_story_bible_model: str = Form(""),
    route_outline_provider: str = Form(""),
    route_outline_model: str = Form(""),
    route_chapter_plan_provider: str = Form(""),
    route_chapter_plan_model: str = Form(""),
    route_chapter_draft_provider: str = Form(""),
    route_chapter_draft_model: str = Form(""),
    route_chapter_critique_provider: str = Form(""),
    route_chapter_critique_model: str = Form(""),
    route_chapter_revision_provider: str = Form(""),
    route_chapter_revision_model: str = Form(""),
    route_chapter_summary_provider: str = Form(""),
    route_chapter_summary_model: str = Form(""),
    route_continuity_update_provider: str = Form(""),
    route_continuity_update_model: str = Form(""),
    route_manuscript_qa_provider: str = Form(""),
    route_manuscript_qa_model: str = Form(""),
    story_setting: str = Form(""),
    story_tone: str = Form(""),
    story_protagonist: str = Form(""),
    story_supporting_cast: str = Form(""),
    story_antagonist: str = Form(""),
    story_core_conflict: str = Form(""),
    story_ending_target: str = Form(""),
    story_world_rules: str = Form(""),
    story_must_include: str = Form(""),
    story_avoid: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    raw_form_values = {
        "title": title,
        "premise": premise,
        "desired_word_count": desired_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "preferred_provider_name": preferred_provider_name,
        "preferred_model": preferred_model,
        "notes": notes,
        "route_story_bible_provider": route_story_bible_provider,
        "route_story_bible_model": route_story_bible_model,
        "route_outline_provider": route_outline_provider,
        "route_outline_model": route_outline_model,
        "route_chapter_plan_provider": route_chapter_plan_provider,
        "route_chapter_plan_model": route_chapter_plan_model,
        "route_chapter_draft_provider": route_chapter_draft_provider,
        "route_chapter_draft_model": route_chapter_draft_model,
        "route_chapter_critique_provider": route_chapter_critique_provider,
        "route_chapter_critique_model": route_chapter_critique_model,
        "route_chapter_revision_provider": route_chapter_revision_provider,
        "route_chapter_revision_model": route_chapter_revision_model,
        "route_chapter_summary_provider": route_chapter_summary_provider,
        "route_chapter_summary_model": route_chapter_summary_model,
        "route_continuity_update_provider": route_continuity_update_provider,
        "route_continuity_update_model": route_continuity_update_model,
        "route_manuscript_qa_provider": route_manuscript_qa_provider,
        "route_manuscript_qa_model": route_manuscript_qa_model,
        "story_setting": story_setting,
        "story_tone": story_tone,
        "story_protagonist": story_protagonist,
        "story_supporting_cast": story_supporting_cast,
        "story_antagonist": story_antagonist,
        "story_core_conflict": story_core_conflict,
        "story_ending_target": story_ending_target,
        "story_world_rules": story_world_rules,
        "story_must_include": story_must_include,
        "story_avoid": story_avoid,
    }
    payload_values = {
        "title": title,
        "premise": premise,
        "desired_word_count": desired_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "preferred_provider_name": preferred_provider_name,
        "preferred_model": preferred_model,
        "notes": notes,
        "story_brief": _story_brief_payload(raw_form_values),
        "task_routing": _task_routing_payload(raw_form_values),
    }
    provider_config, provider_status, _ = _provider_status(settings, db)
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
    try:
        payload = ProjectCreate.model_validate(payload_values)
    except ValidationError as exc:
        return _render(
            request,
            "project_new.html",
            _project_new_context(request, db, settings, form_values=raw_form_values, form_errors=_field_errors(exc)),
            status_code=400,
        )

    errors: dict[str, str] = {}
    provider_error = _validate_provider_selection(
        payload.preferred_provider_name,
        payload.preferred_model,
        provider_configs_by_name,
        provider_statuses_by_name,
    )
    if provider_error:
        errors["preferred_model"] = provider_error
    errors.update(_validate_task_routing(payload.task_routing.model_dump(exclude_none=True), provider_configs_by_name, provider_statuses_by_name))
    if errors:
        return _render(
            request,
            "project_new.html",
            _project_new_context(request, db, settings, form_values=raw_form_values, form_errors=errors),
            status_code=400,
        )

    payload = payload.model_copy(update={"notes": payload.notes or None})
    project = create_project(db, payload)
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Project created.", message_tone="success")


@router.get("/settings/provider")
def provider_settings_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    return _render(request, "provider_settings.html", _provider_settings_context(request, db, settings))


@router.post("/settings/providers/ollama")
@router.post("/settings/providers/{provider_name}")
def update_provider_ui(
    request: Request,
    provider_name: str = "ollama",
    base_url: str = Form(""),
    default_model: str = Form(""),
    api_key: str = Form(""),
    is_enabled: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    raw_values = {
        provider_name: {
            "base_url": base_url,
            "default_model": default_model,
            "api_key": api_key,
            "is_enabled": True if provider_name == "ollama" and is_enabled is None else _coerce_checkbox(is_enabled),
        }
    }
    try:
        payload = ProviderConfigUpdate.model_validate(raw_values[provider_name])
    except ValidationError as exc:
        return _render(
            request,
            "provider_settings.html",
            _provider_settings_context(
                request,
                db,
                settings,
                form_values=raw_values,
                form_errors={provider_name: _field_errors(exc)},
            ),
            status_code=400,
        )

    preview_status, _ = _provider_preview(
        settings,
        provider_name,
        payload.base_url,
        payload.default_model,
        api_key=payload.api_key,
        is_enabled=payload.is_enabled,
    )
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
                form_errors={provider_name: errors},
                page_error="These changes were tested but not saved yet.",
                provider_status=preview_status,
            ),
            status_code=400,
        )

    update_provider_config(db, settings, provider_name, payload)
    db.commit()
    if preview_status.reachable:
        return _redirect("/settings/provider", message="Provider settings saved.", message_tone="success")
    return _redirect(
        "/settings/provider",
        message=f"Provider settings saved. Novel Generator still cannot reach {provider_definition(provider_name).label} at that address yet.",
        message_tone="warning",
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
    preferred_provider_name: str = Form("ollama"),
    preferred_model: str = Form(""),
    notes: str = Form(""),
    route_story_bible_provider: str = Form(""),
    route_story_bible_model: str = Form(""),
    route_outline_provider: str = Form(""),
    route_outline_model: str = Form(""),
    route_chapter_plan_provider: str = Form(""),
    route_chapter_plan_model: str = Form(""),
    route_chapter_draft_provider: str = Form(""),
    route_chapter_draft_model: str = Form(""),
    route_chapter_critique_provider: str = Form(""),
    route_chapter_critique_model: str = Form(""),
    route_chapter_revision_provider: str = Form(""),
    route_chapter_revision_model: str = Form(""),
    route_chapter_summary_provider: str = Form(""),
    route_chapter_summary_model: str = Form(""),
    route_continuity_update_provider: str = Form(""),
    route_continuity_update_model: str = Form(""),
    route_manuscript_qa_provider: str = Form(""),
    route_manuscript_qa_model: str = Form(""),
    story_setting: str = Form(""),
    story_tone: str = Form(""),
    story_protagonist: str = Form(""),
    story_supporting_cast: str = Form(""),
    story_antagonist: str = Form(""),
    story_core_conflict: str = Form(""),
    story_ending_target: str = Form(""),
    story_world_rules: str = Form(""),
    story_must_include: str = Form(""),
    story_avoid: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    raw_form_values = {
        "title": title,
        "premise": premise,
        "desired_word_count": desired_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "preferred_provider_name": preferred_provider_name,
        "preferred_model": preferred_model,
        "notes": notes,
        "route_story_bible_provider": route_story_bible_provider,
        "route_story_bible_model": route_story_bible_model,
        "route_outline_provider": route_outline_provider,
        "route_outline_model": route_outline_model,
        "route_chapter_plan_provider": route_chapter_plan_provider,
        "route_chapter_plan_model": route_chapter_plan_model,
        "route_chapter_draft_provider": route_chapter_draft_provider,
        "route_chapter_draft_model": route_chapter_draft_model,
        "route_chapter_critique_provider": route_chapter_critique_provider,
        "route_chapter_critique_model": route_chapter_critique_model,
        "route_chapter_revision_provider": route_chapter_revision_provider,
        "route_chapter_revision_model": route_chapter_revision_model,
        "route_chapter_summary_provider": route_chapter_summary_provider,
        "route_chapter_summary_model": route_chapter_summary_model,
        "route_continuity_update_provider": route_continuity_update_provider,
        "route_continuity_update_model": route_continuity_update_model,
        "route_manuscript_qa_provider": route_manuscript_qa_provider,
        "route_manuscript_qa_model": route_manuscript_qa_model,
        "story_setting": story_setting,
        "story_tone": story_tone,
        "story_protagonist": story_protagonist,
        "story_supporting_cast": story_supporting_cast,
        "story_antagonist": story_antagonist,
        "story_core_conflict": story_core_conflict,
        "story_ending_target": story_ending_target,
        "story_world_rules": story_world_rules,
        "story_must_include": story_must_include,
        "story_avoid": story_avoid,
    }
    payload_values = {
        "title": title,
        "premise": premise,
        "desired_word_count": desired_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "preferred_provider_name": preferred_provider_name,
        "preferred_model": preferred_model,
        "notes": notes,
        "story_brief": _story_brief_payload(raw_form_values),
        "task_routing": _task_routing_payload(raw_form_values),
    }
    provider_configs_by_name, provider_statuses_by_name, _ = _provider_catalog(settings, db)
    try:
        validated = ProjectCreate.model_validate(payload_values)
    except ValidationError as exc:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                edit_values=raw_form_values,
                edit_errors=_field_errors(exc),
                open_edit_form=True,
            ),
            status_code=400,
        )

    errors: dict[str, str] = {}
    provider_error = _validate_provider_selection(
        validated.preferred_provider_name,
        validated.preferred_model,
        provider_configs_by_name,
        provider_statuses_by_name,
    )
    if provider_error:
        errors["preferred_model"] = provider_error
    errors.update(
        _validate_task_routing(
            validated.task_routing.model_dump(exclude_none=True),
            provider_configs_by_name,
            provider_statuses_by_name,
        )
    )
    if errors:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(
                request,
                project,
                db,
                settings,
                edit_values=raw_form_values,
                edit_errors=errors,
                open_edit_form=True,
            ),
            status_code=400,
        )

    payload = ProjectUpdate(**validated.model_dump())
    update_project(db, project, payload)
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Project defaults updated.", message_tone="success")


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
            message_tone="warning",
        )

    run_ids = delete_project(db, project)
    db.commit()
    delete_run_artifacts_dirs(settings.artifacts_dir, run_ids)
    return _redirect("/projects", message="Project deleted.", message_tone="success")


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
        return _redirect(
            f"/projects/{project_id}",
            message="No finished runs were available to delete.",
            message_tone="warning",
        )
    return _redirect(
        f"/projects/{project_id}",
        message=f"Deleted {len(deleted_run_ids)} finished run(s).",
        message_tone="success",
    )


@router.post("/projects/{project_id}/runs/new")
def create_run_ui(
    project_id: str,
    request: Request,
    provider_name: str = Form("ollama"),
    model_name: str = Form(""),
    target_word_count: str = Form(""),
    requested_chapters: str = Form(""),
    min_words_per_chapter: str = Form(""),
    max_words_per_chapter: str = Form(""),
    pause_after_outline: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    raw_values = {
        "provider_name": provider_name,
        "model_name": model_name,
        "target_word_count": target_word_count,
        "requested_chapters": requested_chapters,
        "min_words_per_chapter": min_words_per_chapter,
        "max_words_per_chapter": max_words_per_chapter,
        "pause_after_outline": _coerce_checkbox(pause_after_outline),
    }
    provider_configs_by_name, provider_statuses_by_name, manager = _provider_catalog(settings, db)

    try:
        payload = RunCreate.model_validate({"project_id": project_id, **raw_values, "task_routing": project.task_routing or {}})
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

    chosen_provider = (payload.provider_name or project.preferred_provider_name or "ollama").strip()
    chosen_model = (payload.model_name or project.preferred_model).strip()
    errors: dict[str, str] = {}
    provider_error = _validate_provider_selection(
        chosen_provider,
        chosen_model,
        provider_configs_by_name,
        provider_statuses_by_name,
    )
    if provider_error:
        errors["model_name"] = provider_error
    if errors:
        return _render(
            request,
            "project_detail.html",
            _project_detail_context(request, project, db, settings, run_values=raw_values, run_errors=errors),
            status_code=400,
        )

    payload = payload.model_copy(update={"provider_name": chosen_provider, "model_name": chosen_model, "task_routing": project.task_routing or {}})
    try:
        manager.ensure_model(chosen_provider, chosen_model)
        run = create_run(db, project, payload)
    except (ProviderError, ProviderTransportError, ValueError) as exc:
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
    record_event(
        db,
        run,
        "run_queued",
        {"message": "Run queued from the web UI.", "provider_name": chosen_provider, "model_name": chosen_model},
    )
    db.commit()
    return _redirect(f"/runs/{run.id}", message="Run queued.", message_tone="success")


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
            "awaiting_outline_approval": run.status == RunStatus.AWAITING_APPROVAL,
            "outline_entries": list(run.outline or []),
            "story_bible": run.story_bible or {},
            "continuity_ledger": run.continuity_ledger or {},
            **_artifact_context(run),
            **_run_dashboard_context(run),
        },
    )


@router.post("/runs/{run_id}/approve-outline")
def approve_outline_ui(run_id: str, db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        approve_outline_review(db, run)
    except ValueError as exc:
        return _redirect(f"/runs/{run_id}", message=str(exc), message_tone="warning")
    db.commit()
    return _redirect(
        f"/runs/{run.id}",
        message="Outline approved. The worker can continue drafting the manuscript now.",
        message_tone="success",
    )


@router.post("/runs/{run_id}/cancel-and-edit")
def cancel_and_edit_run_ui(run_id: str, db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != RunStatus.AWAITING_APPROVAL:
        return _redirect(
            f"/runs/{run.id}",
            message="Only runs waiting for outline approval can be sent back for project edits.",
            message_tone="warning",
        )
    request_run_cancellation(db, run)
    db.commit()
    return _redirect(
        f"/projects/{run.project_id}",
        message="Outline review canceled. The project edit form is open so you can adjust the brief or defaults.",
        message_tone="warning",
        open_edit="1",
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
        return _redirect(
            f"/runs/{run.id}",
            message="Wait for the current run to finish before re-queueing it.",
            message_tone="warning",
        )

    _, _, manager = _provider_catalog(settings, db)
    try:
        manager.ensure_model(run.provider_name, run.model_name)
    except (ProviderError, ProviderTransportError) as exc:
        return _redirect(f"/runs/{run.id}", message=str(exc), message_tone="error")

    payload = _same_settings_payload(run)
    try:
        new_run = create_run(db, run.project, payload)
    except ValueError as exc:
        return _redirect(f"/runs/{run.id}", message=str(exc), message_tone="error")
    record_event(
        db,
        new_run,
        "run_queued",
        {"message": "Run re-queued with the same settings.", "provider_name": run.provider_name, "model_name": run.model_name},
    )
    db.commit()
    return _redirect(f"/runs/{new_run.id}", message="Run re-queued.", message_tone="success")


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
        return _redirect(f"/runs/{run.id}", message="Only finished runs can be deleted.", message_tone="warning")

    project_id = run.project_id
    deleted_run_id = delete_run(db, run)
    db.commit()
    delete_run_artifacts_dir(settings.artifacts_dir, deleted_run_id)
    return _redirect(f"/projects/{project_id}", message="Run deleted.", message_tone="success")


@router.post("/runs/{run_id}/cancel")
def cancel_run_ui(run_id: str, db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    request_run_cancellation(db, run)
    db.commit()
    return _redirect(f"/runs/{run.id}", message="Cancellation requested.", message_tone="success")


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
        return _redirect(
            f"/runs/{run_id}",
            message="Wait for the current run to finish before regenerating.",
            message_tone="warning",
        )
    _, _, manager = _provider_catalog(settings, db)
    try:
        manager.ensure_model(run.provider_name, run.model_name)
    except (ProviderError, ProviderTransportError) as exc:
        return _redirect(f"/runs/{run_id}", message=str(exc), message_tone="error")
    payload = _same_settings_payload(run, source_run_id=run.id, resume_from_chapter=chapter_number)
    try:
        new_run = create_run(db, run.project, payload)
    except ValueError as exc:
        return _redirect(f"/runs/{run_id}", message=str(exc), message_tone="error")
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
    return _redirect(
        f"/runs/{new_run.id}",
        message=f"Queued regeneration from chapter {chapter_number}.",
        message_tone="success",
    )
