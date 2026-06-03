from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import get_app_settings, get_db, get_templates
from ..models import ChapterStatus, GenerationRun, Project, RunStageAttempt, RunStatus
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
from ..schemas import (
    QUALITY_PROFILE_VALUES,
    CanonicalEntity,
    ProjectCreate,
    ProjectUpdate,
    ProviderCapabilities,
    ProviderConfigUpdate,
    RunCreate,
)
from ..services.genre_profiles import genre_profile, genre_profile_options
from ..services.openai_compatible import OpenAICompatibleClient
from ..services.ollama import OllamaClient
from ..services.exports import export_publication_artifact, publication_export_options
from ..services.provider_errors import ProviderError, ProviderTransportError
from ..services.providers import ProviderManager, TASK_ROUTE_STAGES, provider_definition, provider_options
from ..services.state import approve_outline_review, request_run_cancellation, resume_failed_run
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
        "id": "developmental_rewrite",
        "label": "Developmental rewrite",
        "description": "Creating the standard full-manuscript structural rewrite plan.",
        "why": "This stage turns QA risks into chapter-level keep, merge, cut, bridge, reorder, or rewrite actions before the final edit pass.",
        "result": "Rewrite report, revised-outline, and QA comparison artifacts should appear beside the manuscript exports.",
    },
    {
        "id": "developmental_revision",
        "label": "Developmental revision",
        "description": "Applying targeted structural chapter actions before line editing.",
        "why": "The worker is using the developmental plan to rewrite only chapters that need a stronger story turn, bridge, compression, or permanent consequence.",
        "result": "Targeted chapters should gain safer before/after revision events while unchanged chapters pass through to final edit.",
    },
    {
        "id": "chapter_humanization",
        "label": "Humanize chapter",
        "description": "Adding ordinary human friction and character texture for publication mode.",
        "why": "The worker is reducing allegorical or discipline-only characterization while preserving the chapter's canon outcome.",
        "result": "Targeted chapters should gain more private wants, subtext, and believable interpersonal pressure.",
    },
    {
        "id": "chapter_compression",
        "label": "Compress prose",
        "description": "Cutting repeated motifs, filler explanation, and generated-feeling density.",
        "why": "Publication mode drafts long enough to leave material for a later compression pass, then trims repeated atmosphere and over-explanation.",
        "result": "Targeted chapters should move closer to the final word target with cleaner prose.",
    },
    {
        "id": "chapter_edit",
        "label": "Final edit",
        "description": "Polishing saved chapter prose before final QA and export.",
        "why": "The worker is line-editing each chapter for clarity, rhythm, concrete sensory detail, and cleaner transitions while preserving the story state.",
        "result": "Chapter text and word counts should update without changing the approved outline or continuity checkpoints.",
    },
    {
        "id": "publication_readiness",
        "label": "Readiness QA",
        "description": "Scoring the manuscript against publication-readiness criteria.",
        "why": "Publication mode distinguishes a reviewable manuscript from one that is actually ready for publication layout.",
        "result": "The final QA report should show publication-readiness scores and whether more editorial work is needed.",
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

CANON_ENTITY_TYPES = [
    "person",
    "faction",
    "system",
    "project",
    "location",
    "artifact",
    "organization",
    "technology",
    "event",
]
LOCAL_PROVIDER_NAMES = {"ollama"}
HIGH_TOKEN_ROUTE_STAGES = {stage["id"] for stage in TASK_ROUTE_STAGES}
LOCAL_PROVIDER_DISCLOSURE = (
    "Runs locally on your configured Ollama host. Manuscript text is not sent to a cloud provider by Novel Generator."
)
EXTERNAL_PROVIDER_DISCLOSURE = (
    "This route may send manuscript text and story data to the configured external provider. "
    "Check that provider's privacy, retention, and pricing policies before using it."
)
HIGH_TOKEN_DISCLOSURE = (
    "Full novel runs can use large prompts, chapter drafts, critiques, and QA payloads. "
    "Expect high token usage if the configured provider charges by token."
)
PREFLIGHT_OUTLINE_CHUNK_THRESHOLD = 32
PREFLIGHT_OUTLINE_CHUNK_SIZE = 8
QUALITY_PROFILE_DEFS = [
    {
        "value": "balanced",
        "label": "Balanced",
        "summary": "Current behavior",
        "description": "Use the existing revision thresholds, standard developmental planning, and the final edit pass.",
    },
    {
        "value": "draft",
        "label": "Draft",
        "summary": "Fastest full manuscript",
        "description": "Defer non-blocking polish repairs so long runs reach a complete draft sooner.",
    },
    {
        "value": "strict",
        "label": "Strict",
        "summary": "Most editorial scrutiny",
        "description": "Use tighter revision thresholds with standard developmental planning and the final edit pass.",
    },
    {
        "value": "publication",
        "label": "Publication",
        "summary": "Highest-cost editorial path",
        "description": "Draft long, force developmental actions, humanize characters, compress repeated prose, run final readiness QA, and require outline approval.",
    },
]
QUALITY_PROFILE_LOOKUP = {item["value"]: item for item in QUALITY_PROFILE_DEFS}
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
    "developmental_rewrite",
    "developmental_revision",
    "chapter_humanization",
    "chapter_compression",
    "chapter_edit",
    "publication_readiness",
    "export",
    "completed",
]
CALIBRATION_STAGE_GROUPS = [
    {
        "id": "structured",
        "label": "Structured/support",
        "description": "JSON-heavy planning, continuity, QA, and editorial control stages.",
        "stages": {
            "story_bible",
            "outline",
            "chapter_plan",
            "chapter_critique",
            "chapter_summary",
            "continuity_update",
            "manuscript_qa",
            "developmental_rewrite",
        },
    },
    {
        "id": "prose",
        "label": "Prose/editing",
        "description": "Longer creative drafting, targeted rewrite, and final chapter polish stages.",
        "stages": {
            "chapter_draft",
            "chapter_revision",
            "developmental_revision",
            "chapter_edit",
        },
    },
]
QUALITY_SIGNAL_DEFS = [
    {"field": "forward_motion_score", "label": "Forward motion", "lower_is_better": False},
    {"field": "ending_concreteness_score", "label": "Ending concreteness", "lower_is_better": False},
    {"field": "scene_turn_resolution_score", "label": "Scene turn resolved", "lower_is_better": False, "default": 10},
    {"field": "cost_consequence_realism_score", "label": "Cost realism", "lower_is_better": False},
    {"field": "emotional_depth_score", "label": "Emotional depth", "lower_is_better": False},
    {"field": "side_character_independence_score", "label": "Side-character agency", "lower_is_better": False},
    {"field": "proper_noun_continuity_score", "label": "Proper-noun continuity", "lower_is_better": False},
    {"field": "ideology_clarity_score", "label": "Ideology clarity", "lower_is_better": False},
    {"field": "civilian_texture_score", "label": "Civilian texture", "lower_is_better": False},
    {"field": "genre_contract_score", "label": "Genre contract", "lower_is_better": False, "default": 10},
    {"field": "style_alignment_score", "label": "Style alignment", "lower_is_better": False, "default": 10},
    {"field": "voice_distinctness_score", "label": "Voice distinctness", "lower_is_better": False, "default": 10},
    {"field": "sentence_rhythm_score", "label": "Sentence rhythm", "lower_is_better": False, "default": 10},
    {"field": "sensory_specificity_score", "label": "Sensory specificity", "lower_is_better": False, "default": 10},
    {"field": "dialogue_tension_score", "label": "Dialogue tension", "lower_is_better": False, "default": 10},
    {"field": "repetition_risk_score", "label": "Repetition risk", "lower_is_better": True},
    {"field": "technical_escalation_fatigue_score", "label": "Technical fatigue", "lower_is_better": True},
    {"field": "irreversibility_score", "label": "Irreversibility", "lower_is_better": False, "default": 10},
    {"field": "choice_clarity_score", "label": "Choice clarity", "lower_is_better": False, "default": 10},
    {"field": "cuttable_chapter_risk_score", "label": "Cuttable risk", "lower_is_better": True},
]
COMPARISON_CATEGORY_DEFS = [
    {
        "id": "ending",
        "label": "Ending risks",
        "field": "ending_concreteness_score",
        "threshold": 5,
        "mode": "low",
        "keywords": ["ending", "hook", "abstract"],
    },
    {
        "id": "cost",
        "label": "Easy-win risks",
        "field": "cost_consequence_realism_score",
        "threshold": 5,
        "mode": "low",
        "keywords": ["cost", "price", "technical problem"],
    },
    {
        "id": "side_character",
        "label": "Side-character agency",
        "field": "side_character_independence_score",
        "threshold": 5,
        "mode": "low",
        "keywords": ["side-character", "side character"],
    },
    {
        "id": "repetition",
        "label": "Repetition risks",
        "field": "repetition_risk_score",
        "threshold": 6,
        "mode": "high",
        "keywords": ["repetition", "repeated", "stock phrase"],
    },
    {
        "id": "story_turn",
        "label": "Story-turn risks",
        "field": "cuttable_chapter_risk_score",
        "threshold": 6,
        "mode": "high",
        "keywords": ["story turn", "cuttable", "irreversible"],
    },
    {
        "id": "emotional",
        "label": "Emotional pacing",
        "field": "emotional_depth_score",
        "threshold": 5,
        "mode": "low",
        "keywords": ["emotional", "aftermath", "memory-damage"],
    },
    {
        "id": "continuity",
        "label": "Continuity risks",
        "field": "proper_noun_continuity_score",
        "threshold": 5,
        "mode": "low",
        "keywords": ["proper noun", "continuity", "canonical"],
    },
    {
        "id": "technical",
        "label": "Technical fatigue",
        "field": "technical_escalation_fatigue_score",
        "threshold": 6,
        "mode": "high",
        "keywords": ["technical emergency", "alarm fatigue", "lockdown", "quarantine"],
    },
    {
        "id": "genre",
        "label": "Genre contract",
        "field": "genre_contract_score",
        "threshold": 5,
        "mode": "low",
        "keywords": ["genre"],
    },
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


def _event_display_name(event_type: str) -> str:
    return str(event_type or "update").replace("_", " ")


def _event_summary(event: Any | None) -> dict[str, Any] | None:
    if event is None:
        return None
    payload = event.payload or {}
    summary = (
        payload.get("message")
        or payload.get("title")
        or payload.get("chapter_number")
        or _event_display_name(event.event_type)
    )
    return {
        "sequence": event.sequence,
        "event_type": event.event_type,
        "label": _event_display_name(event.event_type),
        "summary": str(summary),
    }


def _latest_event_summary(run: GenerationRun) -> dict[str, Any] | None:
    events = _sorted_run_events(run)
    return _event_summary(events[-1] if events else None)


def _fallback_event_rows(run: GenerationRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in _sorted_run_events(run):
        if "fallback" not in str(event.event_type):
            continue
        summary = _event_summary(event)
        if summary:
            rows.append(summary)
    return rows[-4:]


def _run_progress_context(run: GenerationRun) -> dict[str, Any]:
    completed_chapters = _chapter_completion_count(run)
    requested_chapters = max(1, int(run.requested_chapters or 1))
    tracked_chapters = len(run.chapters)
    chapter_percent = min(100, int(round((completed_chapters / requested_chapters) * 100)))
    return {
        "completed_chapters": completed_chapters,
        "tracked_chapters": tracked_chapters,
        "requested_chapters": run.requested_chapters,
        "chapter_percent": chapter_percent,
        "word_count": _run_word_count(run),
        "word_percent": _word_progress_percent(run),
        "artifact_count": len(run.artifacts),
        "label": f"{completed_chapters}/{run.requested_chapters} chapters complete",
    }


def _run_health_context(run: GenerationRun) -> dict[str, Any]:
    current_stage, next_stage = _stage_context(run)
    latest_event = _latest_event_summary(run)
    fallback_events = _fallback_event_rows(run)
    progress = _run_progress_context(run)

    if run.status == RunStatus.QUEUED:
        tone = "warning"
        title = "Queued and waiting for the worker"
        body = "The run settings are saved. The next healthy sign is a story-bible event from the worker."
    elif run.status == RunStatus.RUNNING:
        tone = "success" if not fallback_events else "warning"
        title = f"Running: {current_stage['label']}"
        body = current_stage["description"]
    elif run.status == RunStatus.AWAITING_APPROVAL:
        tone = "warning"
        title = "Outline review is waiting on you"
        body = "Approve the outline to start drafting, or cancel and edit the project before the expensive chapter pass begins."
    elif run.status == RunStatus.FAILED:
        tone = "error"
        failure_stage = RUN_STAGE_LOOKUP.get(str(run.current_step or ""), current_stage)
        title = f"Stopped during {failure_stage['label']}"
        body = run.error_message or (latest_event or {}).get("summary") or "Check the latest event before rerunning."
    elif run.status == RunStatus.CANCELED:
        tone = "error"
        title = "Canceled before completion"
        body = "This run is preserved for review. Rerun it, regenerate from a chapter, or delete it when you no longer need the history."
    elif run.status == RunStatus.COMPLETED:
        if progress["completed_chapters"] == run.requested_chapters:
            tone = "success"
            title = "Run completed with all requested chapters"
            body = "Manuscript artifacts, QA feedback, and chapter checkpoints are ready for review."
        else:
            tone = "warning"
            title = "Run completed with incomplete chapter coverage"
            body = "Treat the output as partial and inspect the chapter list before exporting or rerunning."
    else:
        tone = "warning"
        title = f"Run status: {run.status.value.replace('_', ' ')}"
        body = current_stage["description"]

    next_label = next_stage["label"] if next_stage else "No next stage"
    if run.status == RunStatus.AWAITING_APPROVAL:
        next_label = "Approve outline or cancel and edit"
    elif run.status in TERMINAL_STATUSES:
        next_label = "Review outputs and recovery actions"

    return {
        "tone": tone,
        "title": title,
        "body": body,
        "current_stage": current_stage,
        "next_label": next_label,
        "latest_event": latest_event,
        "fallback_events": fallback_events,
        "progress": progress,
    }


def _outline_entry_mapping(entry: Any) -> dict[str, Any]:
    if entry is None:
        return {}
    if hasattr(entry, "model_dump"):
        return entry.model_dump(exclude_none=True)
    if isinstance(entry, dict):
        return entry
    return {}


def _outline_filter_key(value: Any) -> str:
    cleaned = str(value or "Unspecified").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-") or "unspecified"


def _outline_event_range(event: Any) -> tuple[int | None, int | None]:
    payload = event.payload or {}
    start = payload.get("start_chapter") or payload.get("chapter_number")
    end = payload.get("end_chapter") or start
    try:
        start_number = int(start)
        end_number = int(end)
    except (TypeError, ValueError):
        return None, None
    return start_number, max(start_number, end_number)


def _outline_generation_events(run: GenerationRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    visible_events = {
        "story_bible_fallback",
        "outline_chunk_started",
        "outline_chunk_completed",
        "outline_chunk_fallback",
        "outline_validation_fallback",
        "outline_fallback",
        "outline_completed",
        "outline_ready_for_approval",
    }
    for event in _sorted_run_events(run):
        event_type = str(event.event_type or "")
        if event_type not in visible_events and not (event_type.startswith("outline") and "fallback" in event_type):
            continue
        summary = _event_summary(event)
        if not summary:
            continue
        start, end = _outline_event_range(event)
        summary.update(
            {
                "is_fallback": "fallback" in event_type,
                "start_chapter": start,
                "end_chapter": end,
                "range_label": f"Chapters {start}-{end}" if start and end and start != end else (f"Chapter {start}" if start else ""),
            }
        )
        rows.append(summary)
    return rows


def _outline_distribution_rows(values: list[str]) -> list[dict[str, Any]]:
    counts = Counter(value or "Unspecified" for value in values)
    return [
        {"label": label, "key": _outline_filter_key(label), "count": count}
        for label, count in sorted(counts.items(), key=lambda item: (item[0] == "Unspecified", item[0]))
    ]


def _outline_review_context(run: GenerationRun) -> dict[str, Any]:
    entries = [_outline_entry_mapping(entry) for entry in list(run.outline or [])]
    chapter_numbers: list[int] = []
    fallback_events = [event for event in _outline_generation_events(run) if event["is_fallback"]]
    outline_fallback_events = [event for event in fallback_events if str(event["event_type"]).startswith("outline")]
    global_outline_fallback = any(event["start_chapter"] is None for event in outline_fallback_events)
    fallback_ranges = [
        (event["start_chapter"], event["end_chapter"])
        for event in outline_fallback_events
        if event["start_chapter"] is not None and event["end_chapter"] is not None
    ]

    cards: list[dict[str, Any]] = []
    acts: list[str] = []
    modes: list[str] = []
    for fallback_number, entry in enumerate(entries, start=1):
        try:
            chapter_number = int(entry.get("chapter_number") or fallback_number)
        except (TypeError, ValueError):
            chapter_number = fallback_number
        chapter_numbers.append(chapter_number)
        act = str(entry.get("act") or "Unspecified").strip() or "Unspecified"
        mode = str(entry.get("chapter_mode") or "Unspecified").strip() or "Unspecified"
        acts.append(act)
        modes.append(mode)
        ending_hook = entry.get("concrete_ending_hook") or {}
        if hasattr(ending_hook, "model_dump"):
            ending_hook = ending_hook.model_dump(exclude_none=True)
        fallback_hit = global_outline_fallback or any(start <= chapter_number <= end for start, end in fallback_ranges)
        warning_reasons: list[str] = []
        if fallback_hit:
            warning_reasons.append("Generated by fallback")
        if mode == "Unspecified":
            warning_reasons.append("Chapter mode missing")
        if not entry.get("primary_obstacle"):
            warning_reasons.append("Primary obstacle missing")
        if not isinstance(ending_hook, dict) or not (ending_hook.get("trigger") and ending_hook.get("next_problem")):
            warning_reasons.append("Ending hook incomplete")
        cards.append(
            {
                "entry": entry,
                "chapter_number": chapter_number,
                "act": act,
                "act_key": _outline_filter_key(act),
                "chapter_mode": mode,
                "chapter_mode_key": _outline_filter_key(mode),
                "has_warning": bool(warning_reasons),
                "warning_reasons": warning_reasons,
            }
        )

    sequential_numbers = list(range(1, len(entries) + 1))
    missing_numbers = sorted(set(range(1, int(run.requested_chapters or 0) + 1)).difference(chapter_numbers))
    review_warnings: list[str] = []
    if len(entries) != run.requested_chapters:
        review_warnings.append(f"Outline has {len(entries)} chapters, but this run requested {run.requested_chapters}.")
    if chapter_numbers and chapter_numbers != sequential_numbers:
        review_warnings.append("Outline chapter numbers are not sequential.")
    if missing_numbers:
        preview = ", ".join(str(number) for number in missing_numbers[:8])
        suffix = "..." if len(missing_numbers) > 8 else ""
        review_warnings.append(f"Missing chapter anchors: {preview}{suffix}")

    return {
        "entries": entries,
        "cards": cards,
        "chapter_count": len(entries),
        "requested_chapters": run.requested_chapters,
        "chapter_numbers": chapter_numbers,
        "min_chapter": min(chapter_numbers) if chapter_numbers else 1,
        "max_chapter": max(chapter_numbers) if chapter_numbers else run.requested_chapters,
        "act_rows": _outline_distribution_rows(acts),
        "chapter_mode_rows": _outline_distribution_rows(modes),
        "generation_events": _outline_generation_events(run),
        "fallback_events": fallback_events,
        "review_warnings": review_warnings,
        "warning_count": sum(1 for card in cards if card["has_warning"]),
    }


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


def _provider_label(provider_name: str) -> str:
    try:
        return provider_definition(provider_name).label
    except ProviderError:
        return provider_name


def _provider_privacy_disclosure(provider_name: str) -> dict[str, Any]:
    provider_key = str(provider_name or "").strip() or "ollama"
    is_external = provider_key not in LOCAL_PROVIDER_NAMES
    return {
        "provider_name": provider_key,
        "provider_label": _provider_label(provider_key),
        "is_external": is_external,
        "tone": "warning" if is_external else "success",
        "badge_label": "External provider" if is_external else "Local/private",
        "title": "External provider route" if is_external else "Local/private route",
        "body": EXTERNAL_PROVIDER_DISCLOSURE if is_external else LOCAL_PROVIDER_DISCLOSURE,
        "cost_body": HIGH_TOKEN_DISCLOSURE if is_external else "",
    }


def _routing_mapping(task_routing: Any) -> dict[str, Any]:
    if task_routing is None:
        return {}
    if hasattr(task_routing, "model_dump"):
        return task_routing.model_dump(exclude_none=True)
    if isinstance(task_routing, dict):
        return task_routing
    return {}


def _route_entry_mapping(entry: Any) -> dict[str, Any]:
    if entry is None:
        return {}
    if hasattr(entry, "model_dump"):
        return entry.model_dump(exclude_none=True)
    if isinstance(entry, dict):
        return entry
    return {}


def _route_disclosure_rows(
    default_provider_name: str,
    default_model_name: str,
    task_routing: Any,
) -> list[dict[str, Any]]:
    routing = _routing_mapping(task_routing)
    rows: list[dict[str, Any]] = []
    for stage in TASK_ROUTE_STAGES:
        entry = _route_entry_mapping(routing.get(stage["id"]))
        provider_name = str(entry.get("provider_name") or default_provider_name or "ollama").strip()
        model_name = str(entry.get("model_name") or default_model_name or "").strip()
        disclosure = _provider_privacy_disclosure(provider_name)
        rows.append(
            {
                "id": stage["id"],
                "label": stage["label"],
                "provider_name": provider_name,
                "provider_label": disclosure["provider_label"],
                "model_name": model_name,
                "uses_override": bool(entry),
                "is_external": disclosure["is_external"],
                "badge_label": disclosure["badge_label"],
                "tone": disclosure["tone"],
            }
        )
    return rows


def _route_privacy_summary(
    default_provider_name: str,
    default_model_name: str,
    task_routing: Any,
) -> dict[str, Any]:
    rows = _route_disclosure_rows(default_provider_name, default_model_name, task_routing)
    external_rows = [row for row in rows if row["is_external"]]
    if external_rows:
        has_high_token_route = any(row["id"] in HIGH_TOKEN_ROUTE_STAGES for row in external_rows)
        provider_labels = list(dict.fromkeys(row["provider_label"] for row in external_rows))
        return {
            "tone": "warning",
            "title": "External provider disclosure",
            "body": EXTERNAL_PROVIDER_DISCLOSURE,
            "cost_body": HIGH_TOKEN_DISCLOSURE if has_high_token_route else "",
            "badge_label": "External provider",
            "is_external": True,
            "stage_rows": external_rows,
            "provider_labels": provider_labels,
        }
    return {
        "tone": "success",
        "title": "Local/private route disclosure",
        "body": LOCAL_PROVIDER_DISCLOSURE,
        "cost_body": "",
        "badge_label": "Local/private",
        "is_external": False,
        "stage_rows": rows,
        "provider_labels": list(dict.fromkeys(row["provider_label"] for row in rows)),
    }


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
        "independent_side_character_move": outline.get("independent_side_character_move", "")
        or plan.get("independent_side_character_move", ""),
        "chapter_mode": outline.get("chapter_mode", ""),
        "civilian_life_detail": outline.get("civilian_life_detail", ""),
        "emotional_reveal": outline.get("emotional_reveal", ""),
        "ideology_pressure": outline.get("ideology_pressure", ""),
        "genre_specific_beats": outline.get("genre_specific_beats", []),
        "genre_state_change": outline.get("genre_state_change", ""),
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
        "genre_specific_focus": plan.get("genre_specific_focus", ""),
        "planned_genre_specific_beats": plan.get("genre_specific_beats", []),
    }
    return contract, chapter


def _quality_signal_rows(chapter: Any | None) -> list[dict[str, Any]]:
    if chapter is None or not chapter.qa_notes:
        return []
    qa_notes = chapter.qa_notes or {}
    rows: list[dict[str, Any]] = []
    for item in QUALITY_SIGNAL_DEFS:
        score = int(qa_notes.get(item["field"], item.get("default", 0)) or 0)
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
    for item in qa_notes.get("genre_contract_findings", []) or []:
        rows.append({"tone": "neutral", "text": str(item)})
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


def _canon_warning_rows(run: GenerationRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in (run.story_bible or {}).get("canon_registry", []) or []:
        name = str(entity.get("name", "")).strip()
        if name and not entity.get("approved"):
            rows.append({"chapter_number": None, "text": f"Story bible canon is pending approval: {name}."})

    for chapter in sorted(run.chapters, key=lambda item: item.chapter_number):
        qa_notes = chapter.qa_notes or {}
        for bucket in ("warnings", "soft_warnings", "blocking_issues"):
            for item in qa_notes.get(bucket, []) or []:
                text = str(item)
                lowered = text.lower()
                if "canon" in lowered or "proper noun" in lowered or "canonical" in lowered:
                    rows.append({"chapter_number": chapter.chapter_number, "text": text})
        for entity in ((chapter.continuity_update or {}).get("new_entities_introduced", []) or []):
            name = str(entity.get("name", "")).strip()
            if name and not entity.get("approved"):
                rows.append(
                    {
                        "chapter_number": chapter.chapter_number,
                        "text": f"Continuity update introduced unapproved canon entity: {name}.",
                    }
                )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str]] = set()
    for row in rows:
        key = (row["chapter_number"], row["text"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:10]


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
    genre_state = [f"{name}: {state}" for name, state in (update.get("genre_state", ledger.get("genre_state", {})) or {}).items()]
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
        "genre_state": genre_state,
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
    current_route = _latest_route_context(run)
    return {
        "current_stage_context": current_stage,
        "next_stage_context": next_stage,
        "current_route_context": current_route,
        "current_route_disclosure": _provider_privacy_disclosure(current_route["provider_name"]),
        "run_route_disclosure": _route_privacy_summary(run.provider_name, run.model_name, run.task_routing),
        "run_route_disclosure_rows": _route_disclosure_rows(run.provider_name, run.model_name, run.task_routing),
        "current_contract": current_contract,
        "quality_signal_rows": _quality_signal_rows(quality_chapter),
        "quality_source_chapter": quality_source,
        "revision_trigger_rows": _revision_trigger_rows(quality_chapter),
        "canon_warning_rows": _canon_warning_rows(run),
        "continuity_snapshot": continuity,
        "total_run_words": _run_word_count(run),
        "word_progress_percent": _word_progress_percent(run),
        "run_stage_data": RUN_STAGES,
        "event_count": len(run.events),
        "run_health": _run_health_context(run),
    }


def _run_row_context(run: GenerationRun) -> dict[str, Any]:
    current_stage, _ = _stage_context(run)
    health = _run_health_context(run)
    route = _latest_route_context(run)
    return {
        "run": run,
        "stage_label": current_stage["label"],
        "health": health,
        "progress": health["progress"],
        "latest_event": health["latest_event"],
        "route": route,
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


def _split_aliases(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[\n,]+", str(value)) if item.strip()]


def _normalize_canon_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _project_canon_entries(project: Project) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entity in (project.story_brief or {}).get("approved_canon", []) or []:
        try:
            payload = CanonicalEntity.model_validate(entity)
        except ValidationError:
            continue
        if not payload.name:
            continue
        entries.append(payload.model_dump())
    return _merge_project_canon_entries(entries)


def _merge_project_canon_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, CanonicalEntity] = {}
    for entity in entries:
        payload = CanonicalEntity.model_validate(entity)
        key = _normalize_canon_key(payload.name)
        if not key:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = payload
            continue
        aliases = sorted({*current.aliases, *payload.aliases})
        merged[key] = current.model_copy(
            update={
                "kind": current.kind or payload.kind,
                "role": current.role or payload.role,
                "aliases": aliases,
                "approved": current.approved or payload.approved,
                "locked": current.locked or payload.locked,
            }
        )
    return [entity.model_dump() for entity in merged.values()]


def _project_canon_rows(project: Project) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, entity in enumerate(_project_canon_entries(project)):
        rows.append({**entity, "index": index, "aliases_text": ", ".join(entity.get("aliases", []))})
    return rows


def _canon_payload_from_form(
    *,
    name: str,
    kind: str,
    role: str,
    aliases: str | list[str] | None,
    approved: bool,
    locked: bool,
) -> dict[str, Any]:
    kind_key = str(kind or "").strip().lower()
    if kind_key not in CANON_ENTITY_TYPES:
        raise ValueError("Choose a supported canon entity type.")
    payload = CanonicalEntity.model_validate(
        {
            "name": name,
            "kind": kind_key,
            "role": role,
            "aliases": _split_aliases(aliases),
            "approved": approved,
            "locked": locked,
        }
    )
    if not payload.name:
        raise ValueError("Canon entities need a name.")
    return payload.model_dump()


def _save_project_canon(db: Session, project: Project, entries: list[dict[str, Any]]) -> None:
    brief = dict(project.story_brief or {})
    brief["approved_canon"] = _merge_project_canon_entries(entries)
    project.story_brief = brief
    db.add(project)
    db.flush()


def _story_brief_form_values(story_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    brief = story_brief or {}
    return {
        "story_genre_profile": genre_profile(brief.get("genre_profile")).id,
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
        "story_style_reference": str(brief.get("style_reference", "") or ""),
        "story_style_targets": _join_lines(brief.get("style_targets", [])),
        "story_dialogue_targets": _join_lines(brief.get("dialogue_targets", [])),
        "story_style_avoid": _join_lines(brief.get("style_avoid", [])),
    }


def _story_brief_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "genre_profile": values.get("story_genre_profile", ""),
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
        "style_reference": values.get("story_style_reference", ""),
        "style_targets": values.get("story_style_targets", ""),
        "dialogue_targets": values.get("story_dialogue_targets", ""),
        "style_avoid": values.get("story_style_avoid", ""),
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
        "developmental_rewrite_enabled": True,
        "quality_profile": "balanced",
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


def _form_route_privacy_summary(
    values: dict[str, Any],
    *,
    provider_field: str,
    model_field: str,
) -> dict[str, Any]:
    return _route_privacy_summary(
        str(values.get(provider_field, "") or "ollama"),
        str(values.get(model_field, "") or ""),
        _task_routing_payload(values),
    )


def _quality_profile_context(value: Any) -> dict[str, str]:
    key = str(value or "balanced").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in QUALITY_PROFILE_VALUES:
        key = "balanced"
    return QUALITY_PROFILE_LOOKUP[key]


def _quality_profile_options(selected: Any) -> list[dict[str, Any]]:
    selected_key = _quality_profile_context(selected)["value"]
    return [
        {
            **option,
            "selected": option["value"] == selected_key,
        }
        for option in QUALITY_PROFILE_DEFS
    ]


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return max(1, fallback)
    return max(1, parsed)


def _run_preflight_context(
    values: dict[str, Any],
    settings: Settings,
    configs_by_name: dict[str, Any],
    statuses_by_name: dict[str, ProviderCapabilities],
) -> dict[str, Any]:
    provider_name = str(values.get("provider_name") or "ollama").strip() or "ollama"
    model_name = str(values.get("model_name") or "").strip()
    requested_chapters = _positive_int(values.get("requested_chapters"), 1)
    target_word_count = _positive_int(values.get("target_word_count"), 1)
    pause_after_outline = bool(values.get("pause_after_outline", True))
    profile = _quality_profile_context(values.get("quality_profile"))
    developmental_rewrite_enabled = bool(values.get("developmental_rewrite_enabled")) or profile["value"] in {"strict", "publication"}
    task_routing = _task_routing_payload(values)
    route_disclosure = _route_privacy_summary(provider_name, model_name, task_routing)
    config = configs_by_name.get(provider_name)
    provider_status = statuses_by_name.get(provider_name)

    outline_chunks = (
        1
        if requested_chapters <= PREFLIGHT_OUTLINE_CHUNK_THRESHOLD
        else max(1, (requested_chapters + PREFLIGHT_OUTLINE_CHUNK_SIZE - 1) // PREFLIGHT_OUTLINE_CHUNK_SIZE)
    )
    estimated_model_calls = 1 + outline_chunks + (requested_chapters * 5) + 1 + requested_chapters + 1
    if developmental_rewrite_enabled:
        estimated_model_calls += 1

    warnings: list[dict[str, str]] = []
    if config is None:
        warnings.append({"tone": "error", "message": "The selected provider is not configured."})
    elif not config.is_enabled:
        warnings.append({"tone": "error", "message": f"{provider_definition(provider_name).label} is disabled."})

    if provider_status is None:
        warnings.append({"tone": "error", "message": "Provider reachability has not been checked yet."})
    elif not provider_status.reachable:
        warnings.append(
            {
                "tone": "error",
                "message": provider_status.error or f"{provider_definition(provider_name).label} is not reachable.",
            }
        )
    elif provider_status.available_models and model_name not in provider_status.available_models:
        warnings.append({"tone": "warning", "message": f"Model '{model_name}' is not in the detected model list."})

    if requested_chapters > PREFLIGHT_OUTLINE_CHUNK_THRESHOLD:
        warnings.append(
            {
                "tone": "warning",
                "message": f"{requested_chapters} chapters will use {outline_chunks} outline chunks to reduce long-outline failures.",
            }
        )
    if requested_chapters >= 64:
        warnings.append(
            {
                "tone": "warning",
                "message": "High chapter counts make checkpoint resume and outline review especially important.",
            }
        )
    if route_disclosure["is_external"]:
        warnings.append(
            {
                "tone": "warning",
                "message": "One or more stages use an external route; verify cost and data-retention expectations before queueing.",
            }
        )
    if not pause_after_outline:
        warnings.append(
            {
                "tone": "warning",
                "message": "Outline approval is disabled, so drafting will begin as soon as the outline is generated.",
            }
        )

    has_error = any(item["tone"] == "error" for item in warnings)
    has_warning = any(item["tone"] == "warning" for item in warnings)
    tone = "error" if has_error else ("warning" if has_warning else "success")
    status_label = "Blocked" if has_error else ("Review" if has_warning else "Ready")
    provider_label = provider_definition(provider_name).label if config is not None else provider_name
    model_status = "Detected"
    if provider_status is None or not provider_status.available_models:
        model_status = "No detected model list"
    elif model_name not in provider_status.available_models:
        model_status = "Not detected"

    return {
        "tone": tone,
        "status_label": status_label,
        "summary": "Provider, model, outline scale, and recovery guardrails checked before queueing.",
        "warnings": warnings,
        "outline_chunks": outline_chunks,
        "estimated_model_calls": estimated_model_calls,
        "rows": [
            {"label": "Provider route", "value": f"{provider_label} / {model_name or '-'}"},
            {"label": "Model reachability", "value": "Reachable" if provider_status and provider_status.reachable else "Unavailable"},
            {"label": "Model status", "value": model_status},
            {"label": "Outline chunks", "value": str(outline_chunks)},
            {"label": "Estimated model calls", "value": f"{estimated_model_calls} minimum"},
            {"label": "Quality profile", "value": profile["label"]},
            {"label": "Target length", "value": f"{requested_chapters} chapters / {target_word_count} words"},
        ],
        "recovery_features": [
            "Stage attempt ledger",
            "Checkpoint resume",
            "Standard developmental planning",
            "Targeted developmental revision waves",
            "Final chapter editing pass",
            f"Stale heartbeat recovery after {max(1, settings.run_stale_after_seconds // 60)} minutes",
        ],
    }


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
                "privacy_label": _provider_privacy_disclosure(option["name"])["badge_label"],
                "is_external": _provider_privacy_disclosure(option["name"])["is_external"],
            }
        )
    return rows


def _stage_display_label(stage_id: str) -> str:
    stage = RUN_STAGE_LOOKUP.get(stage_id)
    if stage:
        return stage["label"]
    return stage_id.replace("_", " ").title()


def _average_int(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _format_calibration_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "-"
    seconds = max(0, duration_ms) / 1000
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    if seconds >= 10:
        return f"{int(round(seconds))}s"
    return f"{seconds:.1f}s"


def _calibration_badge(total: int, success_rate: int | None) -> dict[str, str]:
    if total == 0:
        return {"label": "No evidence", "tone": "queued"}
    if total < 3:
        return {"label": "Needs more data", "tone": "running"}
    if success_rate is not None and success_rate >= 90:
        return {"label": "Strong evidence", "tone": "completed"}
    if success_rate is not None and success_rate >= 75:
        return {"label": "Usable evidence", "tone": "running"}
    return {"label": "Watch failures", "tone": "failed"}


def _attempt_summary(attempts: list[RunStageAttempt]) -> dict[str, Any]:
    total = len(attempts)
    successes = sum(1 for attempt in attempts if attempt.status == "success")
    failures = sum(1 for attempt in attempts if attempt.status == "failed")
    running = sum(1 for attempt in attempts if attempt.status == "running")
    completed_total = successes + failures
    success_rate = int(round((successes / completed_total) * 100)) if completed_total else None
    durations = [int(attempt.duration_ms) for attempt in attempts if attempt.duration_ms is not None]
    output_lengths = [int(attempt.output_chars or 0) for attempt in attempts if attempt.status == "success"]
    badge = _calibration_badge(completed_total, success_rate)
    return {
        "total": total,
        "successes": successes,
        "failures": failures,
        "running": running,
        "completed_total": completed_total,
        "success_rate": success_rate,
        "success_rate_label": f"{success_rate}%" if success_rate is not None else "-",
        "avg_duration_ms": _average_int(durations),
        "avg_duration_label": _format_calibration_duration(_average_int(durations)),
        "avg_output_chars": _average_int(output_lengths),
        "avg_output_label": f"{_average_int(output_lengths):,}" if output_lengths else "-",
        "badge_label": badge["label"],
        "badge_tone": badge["tone"],
    }


def _calibration_recommendation(
    model_label: str,
    summary: dict[str, Any],
    category_summaries: dict[str, dict[str, Any]],
) -> str:
    structured = category_summaries.get("structured", {})
    prose = category_summaries.get("prose", {})
    if summary["failures"] and (summary["success_rate"] or 0) < 75:
        return f"Keep {model_label} manual-only until the failed stages are understood."
    if summary["completed_total"] < 3:
        return f"Benchmark {model_label} on a short run before routing long books to it."
    structured_ready = structured.get("completed_total", 0) >= 3 and (structured.get("success_rate") or 0) >= 85
    prose_ready = prose.get("completed_total", 0) >= 2 and (prose.get("success_rate") or 0) >= 85
    if structured_ready and not prose_ready:
        return f"Candidate for structured/support routing; keep prose stages on the current default."
    if structured_ready and prose_ready:
        return f"Candidate for broader draft-profile routing after one more observed long run."
    if prose_ready:
        return f"Prose/editing stages look promising; gather more structured-stage evidence first."
    return f"Keep routing manual and watch the next run for stage-specific behavior."


def _model_calibration_context(db: Session, *, recent_limit: int = 500) -> dict[str, Any]:
    attempts = list(
        db.scalars(
            select(RunStageAttempt)
            .order_by(RunStageAttempt.started_at.desc(), RunStageAttempt.id.desc())
            .limit(recent_limit)
        )
    )
    finished_attempts = [attempt for attempt in attempts if attempt.status in {"success", "failed"}]
    models: dict[tuple[str, str], dict[str, Any]] = {}
    for attempt in attempts:
        provider_name = str(attempt.provider_name or "unknown").strip() or "unknown"
        model_name = str(attempt.model_name or "unknown").strip() or "unknown"
        key = (provider_name, model_name)
        if key not in models:
            try:
                provider_label = provider_definition(provider_name).label
            except ProviderError:
                provider_label = provider_name
            models[key] = {
                "provider_name": provider_name,
                "provider_label": provider_label,
                "model_name": model_name,
                "attempts": [],
                "stage_attempts": {},
            }
        models[key]["attempts"].append(attempt)
        models[key]["stage_attempts"].setdefault(attempt.stage, []).append(attempt)

    model_rows: list[dict[str, Any]] = []
    for row in models.values():
        summary = _attempt_summary(row["attempts"])
        category_rows: list[dict[str, Any]] = []
        category_summaries: dict[str, dict[str, Any]] = {}
        for group in CALIBRATION_STAGE_GROUPS:
            group_attempts = [attempt for attempt in row["attempts"] if attempt.stage in group["stages"]]
            group_summary = _attempt_summary(group_attempts)
            category_summaries[group["id"]] = group_summary
            category_rows.append({**group, **group_summary})

        stage_rows = []
        for stage_id, stage_attempts in sorted(
            row["stage_attempts"].items(),
            key=lambda item: (-len(item[1]), _stage_display_label(item[0])),
        ):
            stage_rows.append(
                {
                    "stage_id": stage_id,
                    "stage_label": _stage_display_label(stage_id),
                    **_attempt_summary(stage_attempts),
                }
            )

        model_label = f"{row['provider_label']} / {row['model_name']}"
        model_rows.append(
            {
                **row,
                **summary,
                "model_label": model_label,
                "categories": category_rows,
                "stages": stage_rows[:6],
                "recommendation": _calibration_recommendation(model_label, summary, category_summaries),
            }
        )

    model_rows.sort(key=lambda row: (row["completed_total"], row["successes"], row["model_label"]), reverse=True)
    overall = _attempt_summary(finished_attempts)
    return {
        "has_data": bool(finished_attempts),
        "attempt_window": len(attempts),
        "model_count": len(model_rows),
        "models": model_rows[:6],
        "summary": overall,
        "manual_note": "Calibration is advisory only. Provider defaults and per-stage routes change only when you save them.",
    }


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
    completed_runs = [run for run in project_runs if run.status == RunStatus.COMPLETED]
    active_runs = [run for run in project_runs if run.status not in TERMINAL_STATUSES]
    return {
        "project_runs": project_runs,
        "terminal_run_count": len(terminal_runs),
        "completed_run_count": len(completed_runs),
        "active_run_count": len(active_runs),
        "can_delete_project": not active_runs,
    }


def _run_qa_notes(run: GenerationRun) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for chapter in _sorted_run_chapters(run):
        if chapter.qa_notes:
            notes.append(chapter.qa_notes)
    return notes


def _score_average(notes: list[dict[str, Any]], field: str, *, default: int = 0) -> float | None:
    if not notes:
        return None
    scores = [int(item.get(field, default) or default) for item in notes]
    return round(sum(scores) / len(scores), 1)


def _warning_text(qa_notes: dict[str, Any]) -> str:
    warnings = [
        *qa_notes.get("warnings", []),
        *qa_notes.get("soft_warnings", []),
        *qa_notes.get("blocking_issues", []),
        *qa_notes.get("genre_contract_findings", []),
        *qa_notes.get("focus", []),
    ]
    return " ".join(str(item) for item in warnings).lower()


def _category_hit(qa_notes: dict[str, Any], definition: dict[str, Any]) -> bool:
    field = definition.get("field")
    mode = definition.get("mode")
    if field:
        default = 10 if field == "genre_contract_score" else 0
        score = int(qa_notes.get(field, default) or default)
        if mode == "low" and score <= int(definition["threshold"]):
            return True
        if mode == "high" and score >= int(definition["threshold"]):
            return True

    text = _warning_text(qa_notes)
    return any(keyword in text for keyword in definition["keywords"])


def _comparison_category_rows(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for definition in COMPARISON_CATEGORY_DEFS:
        count = sum(1 for item in notes if _category_hit(item, definition))
        rows.append(
            {
                "id": definition["id"],
                "label": definition["label"],
                "count": count,
                "tone": "risk" if count else "healthy",
            }
        )
    return rows


def _comparison_score(notes: list[dict[str, Any]], category_rows: list[dict[str, Any]]) -> int:
    if not notes:
        return 0

    positive_fields = [
        item["field"]
        for item in QUALITY_SIGNAL_DEFS
        if item["field"] != "repetition_risk_score"
    ]
    positive_scores = [
        int(note.get(field, 10 if field == "genre_contract_score" else 0) or 0)
        for note in notes
        for field in positive_fields
    ]
    repetition_scores = [10 - int(note.get("repetition_risk_score", 0) or 0) for note in notes]
    all_scores = [*positive_scores, *repetition_scores]
    quality_average = sum(all_scores) / len(all_scores) if all_scores else 0
    revision_count = sum(
        1
        for note in notes
        if note.get("revision_required") or str(note.get("repair_scope", "none") or "none") != "none"
    )
    warning_load = sum(
        len(note.get("blocking_issues", []) or [])
        + len(note.get("soft_warnings", []) or [])
        + len(note.get("warnings", []) or [])
        for note in notes
    )
    category_load = sum(row["count"] for row in category_rows)
    return max(0, min(100, int(round((quality_average * 10) - (warning_load * 1.5) - revision_count - category_load))))


def _run_comparison_card(run: GenerationRun) -> dict[str, Any]:
    notes = _run_qa_notes(run)
    category_rows = _comparison_category_rows(notes)
    score = _comparison_score(notes, category_rows)
    signal_rows: list[dict[str, Any]] = []
    for definition in QUALITY_SIGNAL_DEFS:
        average = _score_average(
            notes,
            definition["field"],
            default=int(definition.get("default", 0) or 0),
        )
        rounded = int(round(average)) if average is not None else 0
        tone, state_label = _score_state(rounded, lower_is_better=bool(definition["lower_is_better"]))
        signal_rows.append(
            {
                "label": definition["label"],
                "average": average,
                "tone": tone,
                "state_label": state_label if average is not None else "No data",
            }
        )

    qa_artifact = next((artifact for artifact in run.artifacts if artifact.kind == "qa-report"), None)
    manuscript_artifacts = [artifact for artifact in run.artifacts if artifact.kind != "qa-report"]
    revision_count = sum(
        1
        for note in notes
        if note.get("revision_required") or str(note.get("repair_scope", "none") or "none") != "none"
    )
    blocking_count = sum(len(note.get("blocking_issues", []) or []) for note in notes)
    warning_count = sum(
        len(note.get("warnings", []) or []) + len(note.get("soft_warnings", []) or [])
        for note in notes
    )
    standout_strengths = [
        str(item)
        for note in notes
        for item in note.get("strengths", []) or []
    ][:3]
    top_risks = [
        row["label"]
        for row in category_rows
        if row["count"]
    ][:3]
    return {
        "run": run,
        "short_id": run.id[:8],
        "provider_model": f"{run.provider_name} / {run.model_name}",
        "completed_chapters": _chapter_completion_count(run),
        "chapter_count": len(run.chapters),
        "word_count": _run_word_count(run),
        "revision_count": revision_count,
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "score": score,
        "signal_rows": signal_rows,
        "category_rows": category_rows,
        "qa_artifact": qa_artifact,
        "manuscript_artifacts": manuscript_artifacts,
        "artifact_count": len(run.artifacts),
        "standout_strengths": standout_strengths,
        "top_risks": top_risks,
    }


def _review_chip(label: str, tone: str = "neutral") -> dict[str, str]:
    return {"label": label, "tone": tone}


def _chapter_final_edit_numbers(run: GenerationRun) -> set[int]:
    numbers: set[int] = set()
    for event in _sorted_run_events(run):
        if event.event_type != "final_chapter_edit_completed":
            continue
        try:
            chapter_number = int((event.payload or {}).get("chapter_number") or 0)
        except (TypeError, ValueError):
            chapter_number = 0
        if chapter_number:
            numbers.add(chapter_number)
    return numbers


def _chapter_status_chips(chapter: Any, *, final_edit_completed: bool = False) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    if chapter.word_count:
        chips.append(_review_chip(f"{chapter.word_count} words", "neutral"))
    if chapter.summary:
        chips.append(_review_chip("Summary saved", "success"))
    if chapter.continuity_update:
        chips.append(_review_chip("Continuity updated", "success"))
    if chapter.qa_notes:
        chips.append(_review_chip("QA stored", "success"))
    if final_edit_completed:
        chips.append(_review_chip("Final edit saved", "success"))
    if chapter.content:
        chips.append(_review_chip("Preview ready", "success"))
    if chapter.error_message:
        chips.append(_review_chip("Chapter error", "risk"))
    return chips


def _chapter_risk_chips(chapter: Any) -> list[dict[str, str]]:
    notes = chapter.qa_notes or {}
    if not notes:
        return []
    chips: list[dict[str, str]] = []
    blocking_count = len(notes.get("blocking_issues", []) or [])
    warning_count = len(notes.get("warnings", []) or []) + len(notes.get("soft_warnings", []) or [])
    if notes.get("revision_required"):
        chips.append(_review_chip("Revision required", "risk"))
    if blocking_count:
        chips.append(_review_chip(f"{blocking_count} blocking", "risk"))
    if warning_count:
        chips.append(_review_chip(f"{warning_count} warnings", "warning"))
    if int(notes.get("ending_concreteness_score", 10) or 10) <= 5:
        chips.append(_review_chip("Weak ending", "risk"))
    if int(notes.get("scene_turn_resolution_score", 10) or 10) <= 5:
        chips.append(_review_chip("Scene turn risk", "warning"))
    if int(notes.get("repetition_risk_score", 0) or 0) >= 7:
        chips.append(_review_chip("Repetition risk", "warning"))
    if int(notes.get("technical_escalation_fatigue_score", 0) or 0) >= 7:
        chips.append(_review_chip("Technical fatigue", "warning"))
    if not chips:
        chips.append(_review_chip("QA steady", "success"))
    return chips


def _chapter_continuity_chips(chapter: Any) -> list[dict[str, str]]:
    update = chapter.continuity_update or {}
    chips: list[dict[str, str]] = []
    if update.get("chapter_outcome"):
        chips.append(_review_chip("Outcome logged", "success"))
    if update.get("open_threads"):
        chips.append(_review_chip(f"{len(update.get('open_threads') or [])} open threads", "warning"))
    if update.get("new_entities_introduced"):
        chips.append(_review_chip(f"{len(update.get('new_entities_introduced') or [])} new entities", "neutral"))
    if update.get("entity_state_changes"):
        chips.append(_review_chip("Entity changes", "neutral"))
    if update.get("trust_fractures"):
        chips.append(_review_chip("Trust fracture", "warning"))
    return chips


def _chapter_review_cards(run: GenerationRun) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    final_edit_numbers = _chapter_final_edit_numbers(run)
    for chapter in _sorted_run_chapters(run):
        risk_chips = _chapter_risk_chips(chapter)
        final_edit_completed = chapter.chapter_number in final_edit_numbers
        cards.append(
            {
                "chapter": chapter,
                "status_chips": _chapter_status_chips(chapter, final_edit_completed=final_edit_completed),
                "risk_chips": risk_chips,
                "continuity_chips": _chapter_continuity_chips(chapter),
                "has_risk": any(chip["tone"] in {"risk", "warning"} for chip in risk_chips),
                "final_edit_completed": final_edit_completed,
            }
        )
    return cards


def _artifact_group_context(run: GenerationRun) -> list[dict[str, Any]]:
    groups = [
        {
            "id": "manuscript",
            "label": "Manuscript exports",
            "description": "Primary draft files generated from the latest saved chapter prose.",
            "empty": "Draft manuscript exports appear after the run completes.",
            "artifacts": [],
        },
        {
            "id": "publication",
            "label": "Publication helpers",
            "description": "Optional ebook and print-helper outputs created from the completed run.",
            "empty": "Create an ebook or print-helper export when this edition is ready for formatting review.",
            "artifacts": [],
        },
        {
            "id": "editorial",
            "label": "Editorial reports",
            "description": "QA and developmental planning artifacts for deciding the next revision move.",
            "empty": "QA and editorial reports appear when their stages finish.",
            "artifacts": [],
        },
    ]
    lookup = {group["id"]: group for group in groups}
    for artifact in sorted(run.artifacts, key=lambda item: (item.kind, item.filename)):
        if artifact.kind in {"markdown", "docx"}:
            lookup["manuscript"]["artifacts"].append(artifact)
        elif artifact.kind.startswith("publication-"):
            lookup["publication"]["artifacts"].append(artifact)
        else:
            lookup["editorial"]["artifacts"].append(artifact)
    return groups


def _manuscript_edition_context(run: GenerationRun, chapter_cards: list[dict[str, Any]]) -> dict[str, Any]:
    chapters = _sorted_run_chapters(run)
    completed_runs = [
        candidate
        for candidate in _sort_runs(run.project)
        if candidate.status == RunStatus.COMPLETED
    ]
    completed_oldest_first = list(reversed(completed_runs))
    completed_ids = [candidate.id for candidate in completed_oldest_first]
    edition_number = (
        completed_ids.index(run.id) + 1
        if run.id in completed_ids
        else len(completed_oldest_first) + 1
    )
    latest_completed = completed_runs[0] if completed_runs else None
    is_current_edition = run.status == RunStatus.COMPLETED and latest_completed is not None and latest_completed.id == run.id
    total_words = _run_word_count(run)
    completed_chapters = len([chapter for chapter in chapters if chapter.status == ChapterStatus.COMPLETED and chapter.content])
    qa_chapters = len([chapter for chapter in chapters if chapter.qa_notes])
    final_edit_numbers = _chapter_final_edit_numbers(run)
    risk_chapters = [card for card in chapter_cards if card["has_risk"]]
    missing_chapters = [chapter for chapter in chapters if not chapter.content]
    manuscript_artifacts = [artifact for artifact in run.artifacts if artifact.kind in {"markdown", "docx"}]
    publication_artifacts = [artifact for artifact in run.artifacts if artifact.kind.startswith("publication-")]
    qa_artifact = next((artifact for artifact in run.artifacts if artifact.kind == "qa-report"), None)
    primary_artifact = next((artifact for artifact in manuscript_artifacts if artifact.kind == "docx"), None) or next(
        (artifact for artifact in manuscript_artifacts if artifact.kind == "markdown"),
        None,
    )

    word_rows: list[dict[str, Any]] = []
    within_target_count = 0
    for card in chapter_cards:
        chapter = card["chapter"]
        word_count = int(chapter.word_count or 0)
        if not chapter.content:
            tone = "pending"
            label = "Missing prose"
        elif word_count < run.min_words_per_chapter:
            tone = "warning"
            label = "Below target"
        elif word_count > run.max_words_per_chapter:
            tone = "warning"
            label = "Above target"
        else:
            tone = "success"
            label = "In range"
            within_target_count += 1
        word_rows.append(
            {
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "word_count": word_count,
                "tone": tone,
                "label": label,
                "has_risk": card["has_risk"],
                "final_edit_completed": card["final_edit_completed"],
                "status": chapter.status.value,
            }
        )

    shortest = min((row for row in word_rows if row["word_count"]), key=lambda row: row["word_count"], default=None)
    longest = max((row for row in word_rows if row["word_count"]), key=lambda row: row["word_count"], default=None)
    average_words = round(total_words / completed_chapters) if completed_chapters else 0

    if run.status == RunStatus.COMPLETED and is_current_edition:
        snapshot_label = "Current generated edition"
    elif run.status == RunStatus.COMPLETED:
        snapshot_label = "Earlier completed edition"
    elif run.status in TERMINAL_STATUSES:
        snapshot_label = "Partial run snapshot"
    else:
        snapshot_label = "In-progress edition draft"

    return {
        "show": bool(chapters) or bool(run.artifacts),
        "snapshot_label": snapshot_label,
        "edition_number": edition_number,
        "is_current_edition": is_current_edition,
        "complete": run.status == RunStatus.COMPLETED and not missing_chapters,
        "total_words": total_words,
        "target_words": run.target_word_count,
        "word_percent": _word_progress_percent(run),
        "average_words": average_words,
        "shortest": shortest,
        "longest": longest,
        "completed_chapters": completed_chapters,
        "requested_chapters": run.requested_chapters,
        "qa_chapters": qa_chapters,
        "final_edit_chapters": len(final_edit_numbers),
        "risk_chapters": len(risk_chapters),
        "missing_chapters": len(missing_chapters),
        "within_target_count": within_target_count,
        "word_rows": word_rows,
        "artifact_groups": _artifact_group_context(run),
        "primary_artifact": primary_artifact,
        "qa_artifact": qa_artifact,
        "publication_artifacts": publication_artifacts,
    }


def _book_completion_context(run: GenerationRun) -> dict[str, Any]:
    chapters = _sorted_run_chapters(run)
    requested_chapters = max(1, run.requested_chapters or len(chapters) or 1)
    outline_count = len(run.outline or [])
    drafted_count = len([chapter for chapter in chapters if chapter.content])
    summary_count = len([chapter for chapter in chapters if chapter.summary])
    continuity_count = len([chapter for chapter in chapters if chapter.continuity_update])
    qa_count = len([chapter for chapter in chapters if chapter.qa_notes])
    qa_artifact_count = len([artifact for artifact in run.artifacts if artifact.kind == "qa-report"])
    manuscript_artifact_count = len([artifact for artifact in run.artifacts if artifact.kind != "qa-report"])
    final_edit_count = 1 if any(event.event_type == "final_editing_completed" for event in run.events) else 0

    def row(label: str, current: int, target: int, *, required: bool = True) -> dict[str, Any]:
        is_complete = current >= target if required else current > 0
        tone = "success" if is_complete else ("warning" if current else "pending")
        return {
            "label": label,
            "current": current,
            "target": target,
            "tone": tone,
            "complete": is_complete,
        }

    rows = [
        row("Story bible", 1 if run.story_bible else 0, 1),
        row("Outline chapters", outline_count, requested_chapters),
        row("Drafted chapters", drafted_count, requested_chapters),
        row("Chapter summaries", summary_count, requested_chapters),
        row("Continuity checkpoints", continuity_count, requested_chapters),
        row("Chapter QA", qa_count, requested_chapters),
        row("Final edit pass", final_edit_count, 1),
        row("Manuscript QA report", qa_artifact_count, 1),
        row("Manuscript exports", manuscript_artifact_count, 1),
    ]
    required_complete = all(item["complete"] for item in rows)
    raw_percent = round((sum(min(item["current"], item["target"]) for item in rows) / sum(item["target"] for item in rows)) * 100)
    percent = raw_percent if required_complete else min(99, raw_percent)
    if run.status == RunStatus.COMPLETED and required_complete:
        title = "Complete book package"
        body = "All requested chapters, continuity checkpoints, QA, final edit, and manuscript exports are present."
    elif run.status == RunStatus.COMPLETED:
        title = "Completed with gaps to review"
        body = "The run reached a terminal state, but one or more book-completion checkpoints is incomplete."
    elif run.status == RunStatus.FAILED:
        title = "Recover from the last checkpoint"
        body = "Use the checklist to see what survived before resuming or regenerating from a chapter."
    elif run.status == RunStatus.AWAITING_APPROVAL:
        title = "Outline approval gate"
        body = "Approve the outline only after the structure looks strong enough to spend the full chapter pass."
    else:
        title = "Building the book package"
        body = "A complete run should finish chapters, checkpoint continuity, run manuscript QA, complete the final edit, run final QA, and export artifacts."

    return {
        "title": title,
        "body": body,
        "rows": rows,
        "percent": percent,
        "complete": required_complete,
    }


def _run_attempt_diagnostics(run: GenerationRun) -> list[dict[str, Any]]:
    attempts = sorted(getattr(run, "stage_attempts", []) or [], key=lambda item: (item.started_at, item.id))
    failed = [attempt for attempt in attempts if attempt.status == "failed"]
    source = failed[-5:] if failed else attempts[-5:]
    return [
        {
            "stage": attempt.stage.replace("_", " "),
            "chapter_number": attempt.chapter_number,
            "status": attempt.status,
            "provider_name": attempt.provider_name,
            "model_name": attempt.model_name,
            "error": attempt.error_message or "",
            "duration_ms": attempt.duration_ms,
        }
        for attempt in source
    ]


def _run_editorial_next_step_context(run: GenerationRun, chapter_cards: list[dict[str, Any]]) -> dict[str, Any]:
    show = run.status in TERMINAL_STATUSES
    comparison_card = _run_comparison_card(run)
    qa_artifact = comparison_card["qa_artifact"]
    completed_runs = [candidate for candidate in _sort_runs(run.project) if candidate.status == RunStatus.COMPLETED]
    top_risks = list(comparison_card["top_risks"])
    if not top_risks:
        top_risks = [
            str(item)
            for note in _run_qa_notes(run)
            for item in [
                *(note.get("blocking_issues", []) or []),
                *(note.get("warnings", []) or []),
                *(note.get("soft_warnings", []) or []),
            ]
        ][:3]

    suggestions: list[dict[str, Any]] = []
    for card in chapter_cards:
        chapter = card["chapter"]
        risk_labels = [chip["label"] for chip in card["risk_chips"] if chip["tone"] in {"risk", "warning"}]
        if risk_labels:
            suggestions.append(
                {
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title,
                    "reason": ", ".join(risk_labels[:2]),
                }
            )
        if len(suggestions) >= 3:
            break

    if run.status == RunStatus.FAILED and not suggestions:
        failed_chapter = next((card["chapter"] for card in chapter_cards if card["chapter"].status == ChapterStatus.FAILED), None)
        chapter_number = (
            failed_chapter.chapter_number
            if failed_chapter
            else run.current_chapter
            or min(run.requested_chapters, _chapter_completion_count(run) + 1)
        )
        suggestions.append(
            {
                "chapter_number": chapter_number,
                "title": f"Chapter {chapter_number}",
                "reason": "Failure stopped the run before a clean handoff.",
            }
        )

    if run.status == RunStatus.COMPLETED:
        title = "Editorial next step"
        body = "Use QA, chapter risks, comparison, and export options to decide whether this draft is ready or needs a targeted rerun."
    elif run.status == RunStatus.FAILED:
        title = "Recovery next step"
        body = run.error_message or "The run stopped before completion. Start with the failure stage, then rerun or regenerate from the safest chapter boundary."
    elif run.status == RunStatus.CANCELED:
        title = "Recovery next step"
        body = "This run was canceled before completion. Review the preserved outline, events, and completed chapters before trying again."
    else:
        title = "Next step"
        body = ""

    return {
        "show": show,
        "title": title,
        "body": body,
        "qa_artifact": qa_artifact,
        "top_risks": top_risks[:3],
        "regenerate_suggestions": suggestions[:3],
        "compare_available": run.status == RunStatus.COMPLETED and len(completed_runs) >= 2,
        "comparison_run_count": len(completed_runs),
        "publication_available": run.status == RunStatus.COMPLETED,
        "resume_available": run.status == RunStatus.FAILED,
        "attempt_diagnostics": _run_attempt_diagnostics(run),
    }


def _project_comparison_context(project: Project) -> dict[str, Any]:
    completed_runs = [
        run
        for run in _sort_runs(project)
        if run.status == RunStatus.COMPLETED
    ]
    cards = [_run_comparison_card(run) for run in completed_runs]
    best_score = max((card["score"] for card in cards), default=None)
    best_run_id = next((card["run"].id for card in cards if card["score"] == best_score), None)
    return {
        "comparison_runs": cards,
        "comparison_run_count": len(cards),
        "best_run_id": best_run_id,
    }


def _artifact_context(run: GenerationRun) -> dict[str, Any]:
    qa_artifact = next((artifact for artifact in run.artifacts if artifact.kind == "qa-report"), None)
    manuscript_artifacts = [artifact for artifact in run.artifacts if artifact.kind != "qa-report"]
    return {
        "qa_artifact": qa_artifact,
        "manuscript_artifacts": manuscript_artifacts,
        "publication_profile_options": publication_export_options(),
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
        "recent_run_rows": [_run_row_context(run) for run in recent_runs],
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
        "genre_profile_options": genre_profile_options(),
        "task_route_rows": _task_route_rows(form_payload),
        "route_disclosure": _form_route_privacy_summary(
            form_payload,
            provider_field="preferred_provider_name",
            model_field="preferred_model",
        ),
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
    project_profile = genre_profile((project.story_brief or {}).get("genre_profile"))
    return {
        "active_nav": "projects",
        "project": project,
        "project_genre_profile": project_profile,
        "canon_entries": _project_canon_rows(project),
        "canon_entity_types": CANON_ENTITY_TYPES,
        **run_stats,
        "provider_config": provider_config,
        "provider_status": provider_status,
        "provider_statuses": provider_statuses_by_name,
        "provider_guidance": _provider_guidance(provider_config.base_url, provider_config.default_model, provider_status, 1),
        "edit_values": edit_payload,
        "edit_errors": edit_errors or {},
        "edit_task_route_rows": _task_route_rows(edit_payload),
        "edit_route_disclosure": _form_route_privacy_summary(
            edit_payload,
            provider_field="preferred_provider_name",
            model_field="preferred_model",
        ),
        "project_run_rows": [_run_row_context(run) for run in run_stats["project_runs"]],
        "run_values": run_payload,
        "run_errors": run_errors or {},
        "quality_profile_options": _quality_profile_options(run_payload.get("quality_profile")),
        "run_preflight": _run_preflight_context(
            run_payload,
            settings,
            provider_configs_by_name,
            provider_statuses_by_name,
        ),
        "run_task_route_rows": _task_route_rows(run_payload),
        "run_route_disclosure": _form_route_privacy_summary(
            run_payload,
            provider_field="provider_name",
            model_field="model_name",
        ),
        "provider_options": _provider_option_rows(provider_configs_by_name, provider_statuses_by_name),
        "genre_profile_options": genre_profile_options(),
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
                "privacy_disclosure": _provider_privacy_disclosure(provider_name),
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
        "model_calibration": _model_calibration_context(db),
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
        developmental_rewrite_enabled=run.developmental_rewrite_enabled,
        quality_profile=run.quality_profile,
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
    story_genre_profile: str = Form("sci_fi_thriller"),
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
    story_style_reference: str = Form(""),
    story_style_targets: str = Form(""),
    story_dialogue_targets: str = Form(""),
    story_style_avoid: str = Form(""),
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
        "story_genre_profile": story_genre_profile,
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
        "story_style_reference": story_style_reference,
        "story_style_targets": story_style_targets,
        "story_dialogue_targets": story_dialogue_targets,
        "story_style_avoid": story_style_avoid,
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


@router.get("/projects/{project_id}/runs/compare")
def compare_project_runs(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return _render(
        request,
        "run_compare.html",
        {
            "active_nav": "projects",
            "project": project,
            **_project_comparison_context(project),
        },
    )


@router.post("/projects/{project_id}/canon")
def add_project_canon_entity(
    project_id: str,
    name: str = Form(""),
    kind: str = Form("person"),
    role: str = Form(""),
    aliases: str = Form(""),
    approved: str | None = Form("1"),
    locked: str | None = Form(None),
    db: Session = Depends(get_db),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        entry = _canon_payload_from_form(
            name=name,
            kind=kind,
            role=role,
            aliases=aliases,
            approved=_coerce_checkbox(approved),
            locked=_coerce_checkbox(locked),
        )
    except (ValidationError, ValueError) as exc:
        return _redirect(f"/projects/{project.id}", message=str(exc), message_tone="warning")
    _save_project_canon(db, project, [*_project_canon_entries(project), entry])
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Canon entity saved.", message_tone="success")


@router.post("/projects/{project_id}/canon/{entity_index}/update")
def update_project_canon_entity(
    project_id: str,
    entity_index: int,
    name: str = Form(""),
    kind: str = Form("person"),
    role: str = Form(""),
    aliases: str = Form(""),
    approved: str | None = Form(None),
    locked: str | None = Form(None),
    db: Session = Depends(get_db),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    entries = _project_canon_entries(project)
    if entity_index < 0 or entity_index >= len(entries):
        return _redirect(f"/projects/{project.id}", message="Canon entity not found.", message_tone="warning")
    try:
        entry = _canon_payload_from_form(
            name=name,
            kind=kind,
            role=role,
            aliases=aliases,
            approved=_coerce_checkbox(approved),
            locked=_coerce_checkbox(locked),
        )
    except (ValidationError, ValueError) as exc:
        return _redirect(f"/projects/{project.id}", message=str(exc), message_tone="warning")
    entries.pop(entity_index)
    _save_project_canon(db, project, [*entries, entry])
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Canon entity updated.", message_tone="success")


@router.post("/projects/{project_id}/canon/{entity_index}/approve")
def approve_project_canon_entity(
    project_id: str,
    entity_index: int,
    db: Session = Depends(get_db),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    entries = _project_canon_entries(project)
    if entity_index < 0 or entity_index >= len(entries):
        return _redirect(f"/projects/{project.id}", message="Canon entity not found.", message_tone="warning")
    entries[entity_index]["approved"] = True
    _save_project_canon(db, project, entries)
    db.commit()
    return _redirect(f"/projects/{project.id}", message="Canon entity approved.", message_tone="success")


@router.post("/projects/{project_id}/canon/{entity_index}/lock")
def toggle_project_canon_entity_lock(
    project_id: str,
    entity_index: int,
    db: Session = Depends(get_db),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    entries = _project_canon_entries(project)
    if entity_index < 0 or entity_index >= len(entries):
        return _redirect(f"/projects/{project.id}", message="Canon entity not found.", message_tone="warning")
    entries[entity_index]["locked"] = not bool(entries[entity_index].get("locked"))
    _save_project_canon(db, project, entries)
    db.commit()
    state = "locked" if entries[entity_index]["locked"] else "unlocked"
    return _redirect(f"/projects/{project.id}", message=f"Canon entity {state}.", message_tone="success")


@router.post("/projects/{project_id}/canon/{entity_index}/delete")
def delete_project_canon_entity(
    project_id: str,
    entity_index: int,
    db: Session = Depends(get_db),
):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    entries = _project_canon_entries(project)
    if entity_index < 0 or entity_index >= len(entries):
        return _redirect(f"/projects/{project.id}", message="Canon entity not found.", message_tone="warning")
    removed = entries.pop(entity_index)
    _save_project_canon(db, project, entries)
    db.commit()
    return _redirect(
        f"/projects/{project.id}",
        message=f"Removed canon entity {removed.get('name', 'entity')}.",
        message_tone="success",
    )


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
    story_genre_profile: str = Form("sci_fi_thriller"),
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
    story_style_reference: str = Form(""),
    story_style_targets: str = Form(""),
    story_dialogue_targets: str = Form(""),
    story_style_avoid: str = Form(""),
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
        "story_genre_profile": story_genre_profile,
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
        "story_style_reference": story_style_reference,
        "story_style_targets": story_style_targets,
        "story_dialogue_targets": story_dialogue_targets,
        "story_style_avoid": story_style_avoid,
    }
    story_brief_payload = _story_brief_payload(raw_form_values)
    story_brief_payload["approved_canon"] = _project_canon_entries(project)
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
        "story_brief": story_brief_payload,
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
    developmental_rewrite_enabled: str | None = Form(None),
    quality_profile: str = Form("balanced"),
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
        "developmental_rewrite_enabled": _coerce_checkbox(developmental_rewrite_enabled),
        "quality_profile": quality_profile,
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
    run_profile = genre_profile((run.story_bible or {}).get("genre_profile") or (run.project.story_brief or {}).get("genre_profile"))
    outline_review = _outline_review_context(run)
    chapter_review_cards = _chapter_review_cards(run)
    return _render(
        request,
        "run_detail.html",
        {
            "active_nav": "projects",
            "run": run,
            "project": run.project,
            "run_genre_profile": run_profile,
            "quality_profile": _quality_profile_context(run.quality_profile),
            "show_rerun": run.status in TERMINAL_STATUSES,
            "terminal_statuses": TERMINAL_STATUSES,
            "run_stages": RUN_STAGES,
            "completed_chapter_count": completed_chapter_count,
            "all_requested_chapters_completed": completed_chapter_count == run.requested_chapters,
            "awaiting_outline_approval": run.status == RunStatus.AWAITING_APPROVAL,
            "outline_entries": outline_review["entries"],
            "outline_review": outline_review,
            "chapter_review_cards": chapter_review_cards,
            "book_completion": _book_completion_context(run),
            "manuscript_edition": _manuscript_edition_context(run, chapter_review_cards),
            "editorial_next_step": _run_editorial_next_step_context(run, chapter_review_cards),
            "story_bible": run.story_bible or {},
            "continuity_ledger": run.continuity_ledger or {},
            **_artifact_context(run),
            **_run_dashboard_context(run),
        },
    )


@router.post("/runs/{run_id}/publication-export")
def publication_export_ui(
    run_id: str,
    profile_id: str = Form(...),
    include_ai_disclosure: str | None = Form(None),
    author_name: str = Form(""),
    copyright_year: str = Form(""),
    publisher: str = Form(""),
    dedication: str = Form(""),
    author_note: str = Form(""),
    isbn: str = Form(""),
    ai_disclosure: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != RunStatus.COMPLETED:
        return _redirect(
            f"/runs/{run.id}",
            message="Publication exports are available after a run completes.",
            message_tone="warning",
        )

    chapters = [
        chapter
        for chapter in _sorted_run_chapters(run)
        if chapter.status == ChapterStatus.COMPLETED and (chapter.content or "").strip()
    ]
    if not chapters:
        return _redirect(
            f"/runs/{run.id}",
            message="No completed chapter prose is available to export yet.",
            message_tone="warning",
        )

    try:
        artifact = export_publication_artifact(
            settings.artifacts_dir,
            run.project,
            run,
            chapters,
            profile_id,
            include_ai_disclosure=_coerce_checkbox(include_ai_disclosure),
            front_matter={
                "author_name": author_name,
                "copyright_year": copyright_year,
                "publisher": publisher,
                "dedication": dedication,
                "author_note": author_note,
                "isbn": isbn,
                "ai_disclosure": ai_disclosure,
            },
        )
    except ValueError as exc:
        return _redirect(f"/runs/{run.id}", message=str(exc), message_tone="warning")

    for existing in list(run.artifacts):
        if existing.filename == artifact.filename and existing.kind == artifact.kind:
            db.delete(existing)
    artifact.run_id = run.id
    db.add(artifact)
    record_event(
        db,
        run,
        "publication_export_created",
        {
            "message": f"Publication export created: {artifact.filename}.",
            "profile_id": profile_id,
            "filename": artifact.filename,
        },
    )
    db.commit()
    return _redirect(
        f"/runs/{run.id}",
        message=f"Publication export created: {artifact.filename}.",
        message_tone="success",
    )


@router.post("/runs/{run_id}/canon/{entity_index}/approve")
def approve_run_canon_entity(
    run_id: str,
    entity_index: int,
    locked: str | None = Form(None),
    db: Session = Depends(get_db),
):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    story_bible = dict(run.story_bible or {})
    canon_registry = list(story_bible.get("canon_registry") or [])
    if entity_index < 0 or entity_index >= len(canon_registry):
        return _redirect(f"/runs/{run.id}", message="Canon entity not found.", message_tone="warning")

    current = CanonicalEntity.model_validate(canon_registry[entity_index])
    approved_entity = current.model_copy(
        update={
            "approved": True,
            "locked": current.locked or _coerce_checkbox(locked),
        }
    )
    canon_registry[entity_index] = approved_entity.model_dump()
    story_bible["canon_registry"] = [
        CanonicalEntity.model_validate(entity).model_dump() for entity in canon_registry
    ]
    run.story_bible = story_bible

    if run.continuity_ledger:
        ledger = dict(run.continuity_ledger)
        updated_active_entities = []
        approved_key = _normalize_canon_key(approved_entity.name)
        for entity in ledger.get("active_entities", []) or []:
            payload = CanonicalEntity.model_validate(entity)
            if _normalize_canon_key(payload.name) == approved_key:
                payload = payload.model_copy(
                    update={"approved": True, "locked": payload.locked or approved_entity.locked}
                )
            updated_active_entities.append(payload.model_dump())
        ledger["active_entities"] = updated_active_entities
        run.continuity_ledger = ledger

    _save_project_canon(db, run.project, [*_project_canon_entries(run.project), approved_entity.model_dump()])
    db.add(run)
    db.commit()
    return _redirect(f"/runs/{run.id}", message="Canon entity approved and added to the project registry.", message_tone="success")


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


@router.post("/runs/{run_id}/resume")
def resume_run_ui(run_id: str, db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        resume_failed_run(db, run)
    except ValueError as exc:
        return _redirect(f"/runs/{run.id}", message=str(exc), message_tone="warning")
    db.commit()
    return _redirect(
        f"/runs/{run.id}",
        message="Run queued to resume from the latest checkpoint.",
        message_tone="success",
    )


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
