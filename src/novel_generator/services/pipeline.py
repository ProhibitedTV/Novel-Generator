from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import ChapterStatus, GenerationRun, RunStatus
from ..repositories import (
    begin_stage_attempt,
    complete_stage_attempt,
    create_chapters_from_outline,
    fail_stage_attempt,
    record_event,
    replace_artifacts,
    touch_run_heartbeat,
)
from ..schemas import (
    ChapterContinuityUpdate,
    ChapterCritique,
    ChapterPlan,
    ChapterStoryTurn,
    CanonicalEntity,
    ContinuityBibleRow,
    ContinuityLedger,
    DevelopmentalRewritePlan,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
    normalize_chapter_mode,
)
from ..settings import Settings
from .editorial import (
    ChapterLintResult,
    detect_canonical_entity_collisions,
    lint_chapter,
    lint_manuscript,
    manuscript_quality_notes,
    merge_canonical_entities,
    render_developmental_qa_comparison_markdown,
    render_developmental_rewrite_report_markdown,
    render_qa_report_markdown,
    render_revised_outline_markdown,
)
from .exports import export_run_artifacts
from .genre_profiles import genre_profile
from .ollama import OllamaClient
from .provider_errors import ProviderError, ProviderTransportError
from .prompts import (
    build_chapter_critique_messages,
    build_chapter_draft_messages,
    build_chapter_edit_messages,
    build_chapter_plan_messages,
    build_chapter_revision_messages,
    build_outline_chunk_messages,
    build_continuity_update_messages,
    build_developmental_rewrite_messages,
    build_json_repair_messages,
    build_manuscript_qa_messages,
    build_outline_messages,
    build_story_bible_messages,
    build_summary_messages,
    parse_chapter_critique,
    parse_chapter_plan,
    parse_continuity_update,
    parse_developmental_rewrite_plan,
    parse_manuscript_qa_report,
    parse_outline,
    parse_outline_chunk,
    parse_story_bible,
    rolling_context,
    sanitize_chapter_content,
)
from .providers import ProviderManager


class RunCanceled(Exception):
    pass


OUTLINE_CHUNK_THRESHOLD = 32
OUTLINE_CHUNK_SIZE = 8


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _dedupe_continuity_bible_table(rows: list[Any]) -> list[ContinuityBibleRow]:
    unique: list[ContinuityBibleRow] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        payload = row if isinstance(row, ContinuityBibleRow) else ContinuityBibleRow.model_validate(row)
        key = (
            payload.item_type,
            payload.name,
            payload.canon_status,
            payload.observed_status,
            payload.notes,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(payload)
    return unique


def _project_approved_canon(project: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entity in (project.story_brief or {}).get("approved_canon", []) or []:
        payload = CanonicalEntity.model_validate(entity)
        if not payload.approved:
            continue
        entries.append(payload.model_dump())
    return entries


def _unapproved_canon_warnings(update: ChapterContinuityUpdate) -> list[str]:
    warnings: list[str] = []
    for entity in update.new_entities_introduced:
        if entity.approved:
            continue
        warnings.append(f"Unapproved canonical entity introduced by continuity update: {entity.name}.")
    return warnings


def _apply_continuity_canon_warnings(chapter: Any, update: ChapterContinuityUpdate) -> None:
    warnings = _unapproved_canon_warnings(update)
    if not warnings:
        return
    critique = ChapterCritique.model_validate(chapter.qa_notes or {})
    score = critique.proper_noun_continuity_score or 10
    chapter.qa_notes = critique.model_copy(
        update={
            "warnings": _dedupe([*critique.warnings, *warnings]),
            "soft_warnings": _dedupe([*critique.soft_warnings, *warnings]),
            "focus": _dedupe([*critique.focus, "Review and approve or merge new canon before continuing too far."]),
            "proper_noun_continuity_score": min(score, 5),
        }
    ).model_dump()


def _sorted_chapters(run: GenerationRun) -> list:
    return sorted(run.chapters, key=lambda item: item.chapter_number)


def _require_requested_chapters(session: Session, run: GenerationRun) -> list:
    session.refresh(run, attribute_names=["chapters"])
    chapters = _sorted_chapters(run)
    if len(chapters) != run.requested_chapters:
        raise RuntimeError(
            f"Run expected {run.requested_chapters} chapter checkpoints, but only {len(chapters)} were available."
        )
    return chapters


def _sync_summary_context(run: GenerationRun, window: int) -> None:
    run.summary_context = rolling_context(run.chapters, window)


def _recent_mode_entries(value: str) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    for part in str(value or "").split(";"):
        part = part.strip()
        if not part.lower().startswith("chapter "):
            continue
        match = re.match(r"chapter\s+(\d+)\s*:\s*(.+)", part, flags=re.IGNORECASE)
        if not match:
            continue
        chapter_number = int(match.group(1))
        mode_text = match.group(2)
        mode = normalize_chapter_mode(mode_text)
        if mode:
            entries.append((chapter_number, mode))
    return entries


def _ledger_with_chapter_mode(
    ledger: ContinuityLedger,
    chapter_number: int,
    chapter_mode: str,
) -> ContinuityLedger:
    mode = normalize_chapter_mode(chapter_mode)
    if not mode:
        return ledger

    recent_entries = [
        entry
        for entry in _recent_mode_entries(ledger.genre_state.get("recent_chapter_modes", ""))
        if entry[0] != chapter_number
    ]
    recent_entries.append((chapter_number, mode))
    recent_entries = recent_entries[-2:]
    recent_rendered = "; ".join(f"Chapter {number}: {entry_mode}" for number, entry_mode in recent_entries)
    return ledger.model_copy(
        update={
            "genre_state": {
                **ledger.genre_state,
                "last_chapter_mode": mode,
                "recent_chapter_modes": recent_rendered,
            }
        }
    )


def _ensure_not_canceled(session: Session, run: GenerationRun) -> None:
    session.refresh(run)
    touch_run_heartbeat(session, run)
    if run.cancel_requested:
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        run.worker_id = None
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_canceled", {"message": "Cancellation was requested."})
        session.commit()
        raise RunCanceled("Run canceled.")


def _generate_structured_output(
    session: Session,
    run: GenerationRun,
    client: ProviderManager | OllamaClient,
    provider_name: str,
    model_name: str,
    build_messages: Callable[[], list[dict[str, str]]],
    parser: Callable[[str], Any],
    label: str,
    stage: str,
    chapter_number: int | None = None,
) -> Any:
    raw_output = _supervised_provider_chat(
        session,
        run,
        client,
        provider_name,
        model_name,
        build_messages(),
        stage=stage,
        chapter_number=chapter_number,
        metadata={"label": label, "phase": "initial"},
    )
    try:
        return parser(raw_output)
    except Exception as exc:
        repaired_output = _supervised_provider_chat(
            session,
            run,
            client,
            provider_name,
            model_name,
            build_json_repair_messages(raw_output, label, str(exc)),
            stage=stage,
            chapter_number=chapter_number,
            metadata={"label": label, "phase": "repair", "repair_error": str(exc)},
        )
        return parser(repaired_output)


def _supervised_provider_chat(
    session: Session,
    run: GenerationRun,
    client: ProviderManager | OllamaClient,
    provider_name: str,
    model_name: str,
    messages: list[dict[str, str]],
    *,
    stage: str,
    chapter_number: int | None = None,
    metadata: dict | None = None,
    stream: bool = False,
) -> str:
    attempt = begin_stage_attempt(
        session,
        run,
        stage=stage,
        chapter_number=chapter_number,
        provider_name=provider_name,
        model_name=model_name,
        metadata=metadata,
    )
    touch_run_heartbeat(session, run)
    session.commit()
    try:
        output = _provider_chat(client, provider_name, model_name, messages, stream=stream)
    except Exception as exc:
        fail_stage_attempt(session, attempt, exc)
        touch_run_heartbeat(session, run)
        session.commit()
        raise
    complete_stage_attempt(session, attempt, output)
    touch_run_heartbeat(session, run)
    session.commit()
    return output


def _provider_chat(
    client: ProviderManager | OllamaClient,
    provider_name: str,
    model_name: str,
    messages: list[dict[str, str]],
    *,
    stream: bool = False,
) -> str:
    if isinstance(client, ProviderManager):
        return client.chat(provider_name, model_name, messages, stream=stream)
    return client.chat(model_name, messages, stream=stream)


def _resolve_stage_route(
    client: ProviderManager | OllamaClient,
    run: GenerationRun,
    stage: str,
) -> tuple[str, str]:
    provider_name = getattr(run, "provider_name", "ollama") or "ollama"
    model_name = run.model_name
    if isinstance(client, ProviderManager):
        return client.route_for(provider_name, model_name, run.task_routing, stage)
    return provider_name, model_name


def _build_initial_ledger(story_bible: StoryBible) -> ContinuityLedger:
    profile = genre_profile(story_bible.genre_profile)
    system_state_by_name = {
        entity.name: entity.role or "Defined in canon registry; initial state not yet changed on page."
        for entity in story_bible.canon_registry
        if entity.name and entity.kind.lower() in {"system", "project"}
    }
    return ContinuityLedger(
        current_patch_status="No irreversible patch decision has been made yet.",
        character_states={
            member.name: f"Starting state: {member.desire} is threatened by {member.risk}."
            for member in story_bible.cast
        },
        world_state="The novel is still in its opening equilibrium.",
        open_threads=[story_bible.logline, story_bible.ending_promise],
        resolved_threads=[],
        timeline=["Opening state established."],
        active_entities=[entity.model_dump() for entity in story_bible.canon_registry],
        entity_state_changes={},
        open_promises_by_name={
            "central_conflict": story_bible.logline,
            "ending_promise": story_bible.ending_promise,
        },
        ideology_state_by_character={
            agenda.name: agenda.public_belief or agenda.stance_on_core_conflict
            for agenda in story_bible.character_agendas
        },
        memory_damage={},
        trust_fractures={},
        civilian_pressure_points=[],
        emotional_open_loops={
            agenda.name: agenda.private_pressure
            for agenda in story_bible.character_agendas
            if agenda.private_pressure
        },
        side_character_decisions={},
        genre_state=dict(profile.default_genre_state),
        system_state_by_name=system_state_by_name,
        system_state_transitions=[],
    )


def _story_bible_from_run(run: GenerationRun) -> StoryBible:
    if not run.story_bible:
        raise RuntimeError("Run is missing a story bible.")
    return StoryBible.model_validate(run.story_bible)


def _continuity_ledger_from_run(run: GenerationRun) -> ContinuityLedger:
    if not run.continuity_ledger:
        raise RuntimeError("Run is missing a continuity ledger.")
    return ContinuityLedger.model_validate(run.continuity_ledger)


def _outline_entry(run: GenerationRun, chapter_number: int) -> StructuredOutlineEntry:
    if not run.outline:
        raise RuntimeError("Run is missing a structured outline.")
    for item in run.outline:
        if int(item.get("chapter_number", 0)) == chapter_number:
            return StructuredOutlineEntry.model_validate(item)
    raise RuntimeError(f"Structured outline entry for chapter {chapter_number} was not found.")


def _ledger_from_update(current_ledger: ContinuityLedger, update: ChapterContinuityUpdate) -> ContinuityLedger:
    collisions = detect_canonical_entity_collisions(current_ledger.active_entities, update.new_entities_introduced)
    if collisions:
        raise RuntimeError("; ".join(collisions))

    timeline = list(update.timeline or current_ledger.timeline)
    if update.timeline_entry and update.timeline_entry not in timeline:
        timeline.append(update.timeline_entry)

    side_character_decisions = {
        name: list(moves)
        for name, moves in current_ledger.side_character_decisions.items()
    }
    for name, moves in update.side_character_decisions.items():
        existing = side_character_decisions.get(name, [])
        side_character_decisions[name] = _dedupe([*existing, *moves])

    system_state_by_name = dict(current_ledger.system_state_by_name)
    system_state_transitions = list(current_ledger.system_state_transitions)
    for transition in update.system_state_transitions:
        if transition.system_name:
            system_state_by_name[transition.system_name] = transition.new_state or system_state_by_name.get(
                transition.system_name,
                "",
            )
        system_state_transitions.append(transition)

    return ContinuityLedger(
        current_patch_status=update.current_patch_status or current_ledger.current_patch_status,
        character_states={**current_ledger.character_states, **update.character_states},
        world_state=update.world_state or current_ledger.world_state,
        open_threads=_dedupe([*current_ledger.open_threads, *update.open_threads]),
        resolved_threads=_dedupe([*current_ledger.resolved_threads, *update.resolved_threads]),
        timeline=timeline,
        active_entities=merge_canonical_entities(current_ledger.active_entities, update.new_entities_introduced),
        entity_state_changes={**current_ledger.entity_state_changes, **update.entity_state_changes},
        open_promises_by_name={**current_ledger.open_promises_by_name, **update.open_promises_by_name},
        ideology_state_by_character={**current_ledger.ideology_state_by_character, **update.ideology_state_by_character},
        memory_damage={**current_ledger.memory_damage, **update.memory_damage},
        trust_fractures={**current_ledger.trust_fractures, **update.trust_fractures},
        civilian_pressure_points=_dedupe([*current_ledger.civilian_pressure_points, *update.civilian_pressure_points]),
        emotional_open_loops={**current_ledger.emotional_open_loops, **update.emotional_open_loops},
        side_character_decisions=side_character_decisions,
        genre_state={**current_ledger.genre_state, **update.genre_state},
        system_state_by_name=system_state_by_name,
        system_state_transitions=system_state_transitions,
    )


def _persist_structured_plan(chapter: Any, plan: ChapterPlan) -> None:
    chapter.plan = json.dumps(plan.model_dump(), indent=2)


def _persist_structured_qa(chapter: Any, critique: ChapterCritique) -> None:
    chapter.qa_notes = critique.model_dump()


def _checkpointed_plan(chapter: Any) -> ChapterPlan | None:
    if not chapter.plan:
        return None
    try:
        return ChapterPlan.model_validate(json.loads(chapter.plan))
    except Exception:
        return None


def _checkpointed_critique(chapter: Any) -> ChapterCritique | None:
    if not chapter.qa_notes:
        return None
    try:
        return ChapterCritique.model_validate(chapter.qa_notes)
    except Exception:
        return None


def _resolve_repair_scope(*scopes: str) -> str:
    if "full_chapter" in scopes:
        return "full_chapter"
    if "targeted_scene_and_ending" in scopes:
        return "targeted_scene_and_ending"
    if "voice_and_texture" in scopes:
        return "voice_and_texture"
    return "none"


STYLE_SCORE_LABELS = {
    "style_alignment_score": "style alignment",
    "voice_distinctness_score": "voice distinctness",
    "sentence_rhythm_score": "sentence rhythm",
    "sensory_specificity_score": "sensory specificity",
    "dialogue_tension_score": "dialogue tension",
}
STYLE_REPAIR_THRESHOLD = 5
STRICT_STYLE_REPAIR_THRESHOLD = 6


def _style_score_warnings(critique: ChapterCritique) -> list[str]:
    weak_scores = [
        f"{label} {getattr(critique, field)}/10"
        for field, label in STYLE_SCORE_LABELS.items()
        if getattr(critique, field) <= STYLE_REPAIR_THRESHOLD
    ]
    if not weak_scores:
        return []
    return ["Style delivery needs a voice-and-texture repair: " + ", ".join(weak_scores) + "."]


ENDING_REPAIR_TYPES = {"abstract_cliffhanger", "image_or_feeling_beat", "outline_summary"}
ENDING_REPAIR_THRESHOLD = 5
TECHNICAL_FATIGUE_REPAIR_THRESHOLD = 6
SIDE_CHARACTER_REPAIR_THRESHOLD = 5
STORY_TURN_REPAIR_THRESHOLD = 5
CUTTABLE_CHAPTER_REPAIR_THRESHOLD = 6
STRICT_LOW_SCORE_THRESHOLD = 6
STRICT_TECHNICAL_FATIGUE_THRESHOLD = 5
STRICT_CUTTABLE_CHAPTER_THRESHOLD = 5


def _ending_score_warnings(critique: ChapterCritique) -> list[str]:
    warnings: list[str] = []
    if critique.ending_hook_type in ENDING_REPAIR_TYPES:
        warnings.append(
            "Ending needs a concrete action hook: "
            f"classified as {critique.ending_hook_type.replace('_', ' ')}."
        )
    if critique.scene_turn_resolution_score <= ENDING_REPAIR_THRESHOLD:
        warnings.append(
            "Ending does not resolve the immediate scene turn: "
            f"scene turn resolution {critique.scene_turn_resolution_score}/10."
        )
    return warnings


def _technical_fatigue_warnings(critique: ChapterCritique) -> list[str]:
    if critique.technical_escalation_fatigue_score < TECHNICAL_FATIGUE_REPAIR_THRESHOLD:
        return []
    return [
        "Technical escalation fatigue needs a targeted repair: "
        f"fatigue score {critique.technical_escalation_fatigue_score}/10."
    ]


def _side_character_warnings(critique: ChapterCritique) -> list[str]:
    if critique.side_character_independence_score > SIDE_CHARACTER_REPAIR_THRESHOLD:
        return []
    return [
        "Side-character agency needs a targeted repair: "
        f"side-character independence {critique.side_character_independence_score}/10."
    ]


def _story_turn_warnings(critique: ChapterCritique) -> list[str]:
    warnings: list[str] = []
    if critique.irreversibility_score <= STORY_TURN_REPAIR_THRESHOLD:
        warnings.append(
            "Story turn needs a stronger irreversible change: "
            f"irreversibility {critique.irreversibility_score}/10."
        )
    if critique.choice_clarity_score <= STORY_TURN_REPAIR_THRESHOLD:
        warnings.append(
            "Story turn needs a clearer protagonist choice: "
            f"choice clarity {critique.choice_clarity_score}/10."
        )
    if critique.cuttable_chapter_risk_score >= CUTTABLE_CHAPTER_REPAIR_THRESHOLD:
        warnings.append(
            "Chapter may be cuttable without damaging manuscript state: "
            f"cuttable risk {critique.cuttable_chapter_risk_score}/10."
        )
    return warnings


def _combine_chapter_feedback(critique: ChapterCritique, lint_result: ChapterLintResult) -> ChapterCritique:
    style_warnings = _style_score_warnings(critique)
    ending_warnings = _ending_score_warnings(critique)
    technical_warnings = _technical_fatigue_warnings(critique)
    side_character_warnings = _side_character_warnings(critique)
    story_turn_warnings = _story_turn_warnings(critique)
    blocking_issues = _dedupe([*critique.blocking_issues, *lint_result.blocking_issues])
    soft_warnings = _dedupe(
        [
            *critique.soft_warnings,
            *lint_result.soft_warnings,
            *style_warnings,
            *ending_warnings,
            *technical_warnings,
            *side_character_warnings,
            *story_turn_warnings,
        ]
    )
    warnings = _dedupe([*critique.warnings, *soft_warnings])
    focus = _dedupe([*critique.focus, *blocking_issues[:2], *soft_warnings[:2]])
    revision_required = (
        critique.revision_required
        or lint_result.needs_repair
        or bool(blocking_issues)
        or bool(style_warnings)
        or bool(ending_warnings)
        or bool(technical_warnings)
        or bool(side_character_warnings)
        or bool(story_turn_warnings)
    )
    repair_scope = _resolve_repair_scope(
        critique.repair_scope,
        lint_result.repair_scope,
        "targeted_scene_and_ending" if ending_warnings else "none",
        "targeted_scene_and_ending" if technical_warnings else "none",
        "targeted_scene_and_ending" if side_character_warnings else "none",
        "targeted_scene_and_ending" if story_turn_warnings else "none",
        "voice_and_texture" if style_warnings else "none",
    )
    return critique.model_copy(
        update={
            "warnings": warnings,
            "revision_required": revision_required,
            "focus": focus,
            "blocking_issues": blocking_issues,
            "soft_warnings": soft_warnings,
            "repair_scope": repair_scope,
        }
    )


def _strict_quality_warnings(critique: ChapterCritique) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    scopes: list[str] = []

    weak_style_scores = [
        f"{label} {getattr(critique, field)}/10"
        for field, label in STYLE_SCORE_LABELS.items()
        if getattr(critique, field) <= STRICT_STYLE_REPAIR_THRESHOLD
    ]
    if weak_style_scores:
        warnings.append("Strict profile wants stronger prose delivery: " + ", ".join(weak_style_scores) + ".")
        scopes.append("voice_and_texture")

    low_score_checks = [
        ("ending_concreteness_score", "ending concreteness"),
        ("scene_turn_resolution_score", "scene turn resolution"),
        ("cost_consequence_realism_score", "cost/consequence realism"),
        ("side_character_independence_score", "side-character independence"),
        ("irreversibility_score", "irreversibility"),
        ("choice_clarity_score", "choice clarity"),
    ]
    for field, label in low_score_checks:
        score = int(getattr(critique, field) or 0)
        if score <= STRICT_LOW_SCORE_THRESHOLD:
            warnings.append(f"Strict profile requires a repair for {label} at {score}/10.")
            scopes.append("targeted_scene_and_ending")

    if critique.technical_escalation_fatigue_score >= STRICT_TECHNICAL_FATIGUE_THRESHOLD:
        warnings.append(
            "Strict profile wants technical escalation pressure reduced: "
            f"fatigue score {critique.technical_escalation_fatigue_score}/10."
        )
        scopes.append("targeted_scene_and_ending")

    if critique.cuttable_chapter_risk_score >= STRICT_CUTTABLE_CHAPTER_THRESHOLD:
        warnings.append(
            "Strict profile wants a more load-bearing chapter turn: "
            f"cuttable risk {critique.cuttable_chapter_risk_score}/10."
        )
        scopes.append("targeted_scene_and_ending")

    return warnings, scopes


def _apply_quality_profile_to_critique(run: GenerationRun, critique: ChapterCritique) -> ChapterCritique:
    profile = str(getattr(run, "quality_profile", "balanced") or "balanced").strip().lower()
    if profile == "draft":
        if critique.blocking_issues or critique.repair_scope == "full_chapter":
            return critique
        draft_note = "Draft profile deferred non-blocking revision work to keep the long run moving."
        return critique.model_copy(
            update={
                "warnings": _dedupe([*critique.warnings, draft_note]),
                "soft_warnings": _dedupe([*critique.soft_warnings, draft_note]),
                "revision_required": False,
                "repair_scope": "none",
            }
        )

    if profile != "strict":
        return critique

    strict_warnings, scopes = _strict_quality_warnings(critique)
    if not strict_warnings:
        return critique

    soft_warnings = _dedupe([*critique.soft_warnings, *strict_warnings])
    warnings = _dedupe([*critique.warnings, *strict_warnings])
    focus = _dedupe([*critique.focus, *strict_warnings[:3]])
    return critique.model_copy(
        update={
            "warnings": warnings,
            "soft_warnings": soft_warnings,
            "focus": focus,
            "revision_required": True,
            "repair_scope": _resolve_repair_scope(critique.repair_scope, *scopes),
        }
    )


def _chapter_prior_context(run: GenerationRun, chapter_number: int) -> list:
    return [
        prior
        for prior in _sorted_chapters(run)
        if prior.chapter_number < chapter_number and (prior.content or prior.summary)
    ]


def _fallback_story_bible(project: Any, run: GenerationRun) -> StoryBible:
    brief = project.story_brief or {}
    profile = genre_profile(brief.get("genre_profile"))
    protagonist = str(brief.get("protagonist") or "The protagonist").strip()
    antagonist = str(brief.get("antagonist") or "the central opposition").strip()
    supporting_cast = [
        str(name).strip()
        for name in brief.get("supporting_cast", [])
        if str(name).strip()
    ]
    cast = [
        {
            "name": protagonist,
            "role": "Protagonist",
            "desire": brief.get("ending_target") or brief.get("core_conflict") or "Resolve the central conflict.",
            "risk": "Losing agency, trust, or the chance to change the outcome.",
        }
    ]
    for name in supporting_cast[:6]:
        cast.append(
            {
                "name": name,
                "role": "Supporting character",
                "desire": "Push the protagonist toward a harder choice.",
                "risk": "Paying a personal cost for the protagonist's progress.",
            }
        )

    agendas = [
        {
            "name": member["name"],
            "want": member["desire"],
            "fear": member["risk"],
            "line_in_sand": "They will not accept an easy solution that violates the story's moral boundary.",
            "stance_on_core_conflict": brief.get("core_conflict") or "The central conflict must cost something real.",
            "relationship_to_protagonist": "Self" if index == 0 else "Pressure-bearing ally or foil",
            "public_belief": brief.get("core_conflict") or "The stated conflict must be confronted directly.",
            "private_pressure": "They fear the final choice will cost more than they can admit.",
            "stress_response": "They narrow into their strongest habit when pressure rises.",
        }
        for index, member in enumerate(cast)
    ]
    canon_registry = [
        {
            "name": protagonist,
            "kind": "person",
            "role": "Protagonist",
            "aliases": [],
            "approved": True,
        }
    ]
    for name in supporting_cast[:6]:
        canon_registry.append(
            {
                "name": name,
                "kind": "person",
                "role": "Supporting character",
                "aliases": [],
                "approved": True,
            }
        )
    if antagonist:
        canon_registry.append(
            {
                "name": antagonist,
                "kind": "faction",
                "role": "Primary opposition force",
                "aliases": [],
                "approved": True,
            }
        )

    return StoryBible.model_validate(
        {
            "genre_profile": profile.id,
            "logline": project.premise,
            "theme": brief.get("core_conflict") or "Every victory should preserve a visible moral cost.",
            "act_plan": [
                "Act I establishes the inciting incident, central cast, and first irreversible commitment.",
                "Act II escalates costs, reversals, and relationship pressure without repeating the premise.",
                "Act III resolves the core conflict through one primary climax and a paid-for ending.",
            ],
            "cast": cast,
            "character_agendas": agendas,
            "canon_registry": canon_registry,
            "conflict_ladder": [
                "The protagonist discovers the central pressure.",
                "The opposition turns progress into a personal or public cost.",
                "The final choice resolves the ending promise at irreversible cost.",
            ],
            "world_rules": list(brief.get("world_rules", [])) or ["Progress must create a visible consequence."],
            "core_system_rules": ["Major systems and factions change state only through on-page causes."],
            "prose_guardrails": [
                "Avoid abstract thesis-statement endings.",
                "Do not let technical wins arrive without cost.",
                "Vary chapter modes and include breathers after intense escalation.",
            ],
            "genre_contract": list(profile.genre_contract),
            "style_profile": {
                "narrative_voice": brief.get("tone") or "Clear close narration with concrete sensory pressure.",
                "sentence_rhythm": "Vary sentence length and avoid repetitive summary cadence.",
                "imagery_palette": list(brief.get("style_targets", [])) or ["concrete objects", "visible consequences"],
                "dialogue_rules": list(brief.get("dialogue_targets", [])) or ["Use pressure and subtext over exposition."],
                "character_voice_map": {
                    member["name"]: "Distinct under pressure; avoid interchangeable dialogue."
                    for member in cast
                },
                "avoid": list(brief.get("style_avoid", [])) or ["generic stakes language"],
            },
            "ending_promise": brief.get("ending_target") or f"{protagonist} must resolve the central conflict at a visible cost.",
        }
    )


def _fallback_chapter_mode(chapter_number: int) -> str:
    pattern = [
        "systems_crisis",
        "investigation",
        "aftermath",
        "interpersonal_confrontation",
        "physical_escape",
        "moral_negotiation",
        "breather",
        "reversal",
    ]
    return pattern[(chapter_number - 1) % len(pattern)]


def _fallback_outcome_type(chapter_number: int, total_chapters: int) -> str:
    if chapter_number == total_chapters:
        return "reversal"
    if chapter_number % 5 == 0:
        return "reversal"
    if chapter_number % 2 == 0:
        return "setback"
    return "compromise"


def _fallback_act(chapter_number: int, total_chapters: int) -> str:
    if chapter_number <= max(1, total_chapters // 4):
        return "Act I"
    if chapter_number < total_chapters:
        return "Act II"
    return "Act III"


def _fallback_outline_entries(
    project: Any,
    story_bible: StoryBible,
    start_chapter: int,
    end_chapter: int,
    total_chapters: int,
) -> list[dict[str, Any]]:
    protagonist = story_bible.cast[0].name if story_bible.cast else "The protagonist"
    support = story_bible.cast[1].name if len(story_bible.cast) > 1 else "a trusted ally"
    canon_name = story_bible.canon_registry[0].name if story_bible.canon_registry else "the central pressure"
    entries: list[dict[str, Any]] = []
    for chapter_number in range(start_chapter, end_chapter + 1):
        mode = _fallback_chapter_mode(chapter_number)
        entries.append(
            {
                "chapter_number": chapter_number,
                "act": _fallback_act(chapter_number, total_chapters),
                "title": f"Pressure Point {chapter_number}",
                "objective": f"{protagonist} pursues step {chapter_number} toward the ending promise without repeating the inciting incident.",
                "conflict_turn": f"{canon_name} forces a new cost in chapter {chapter_number}.",
                "character_turn": f"{protagonist} changes tactics after {support} challenges the price of progress.",
                "reveal": f"A concrete limit or hidden cost of {canon_name} becomes visible.",
                "ending_state": f"The story state after chapter {chapter_number} cannot return to its prior balance.",
                "outcome_type": _fallback_outcome_type(chapter_number, total_chapters),
                "primary_obstacle": f"A specific obstacle blocks the next route to {story_bible.ending_promise}.",
                "cost_if_success": f"Progress in chapter {chapter_number} costs access, trust, safety, or public standing.",
                "side_character_friction": f"{support} resists because the plan endangers something they need.",
                "independent_side_character_move": f"{support} takes an action that changes {protagonist}'s options.",
                "concrete_ending_hook": {
                    "trigger": f"A visible consequence of chapter {chapter_number} arrives.",
                    "visible_object_or_actor": f"{canon_name}",
                    "next_problem": f"The next chapter must answer the cost exposed by chapter {chapter_number}.",
                },
                "chapter_mode": mode,
                "civilian_life_detail": f"Ordinary people adapt around the fallout from chapter {chapter_number}.",
                "emotional_reveal": f"{protagonist} admits a private fear that complicates the next choice.",
                "ideology_pressure": story_bible.theme,
                "genre_specific_beats": [f"Escalate the selected genre contract through chapter {chapter_number}."],
                "genre_state_change": f"The genre pressure advances one irreversible notch in chapter {chapter_number}.",
            }
        )
    return entries


def _fallback_chapter_plan(
    chapter: Any,
    outline_entry: StructuredOutlineEntry,
    story_bible: StoryBible,
) -> ChapterPlan:
    protagonist = story_bible.cast[0].name if story_bible.cast else "The protagonist"
    support = story_bible.cast[1].name if len(story_bible.cast) > 1 else "a supporting character"
    return ChapterPlan(
        chapter_mode=outline_entry.chapter_mode,
        opening_state=outline_entry.objective,
        character_goal=f"{protagonist} pursues the chapter objective without losing agency.",
        scene_beats=[
            outline_entry.objective,
            outline_entry.conflict_turn,
            outline_entry.character_turn,
            outline_entry.reveal,
            outline_entry.ending_state,
        ],
        conflict_turn=outline_entry.conflict_turn,
        ending_hook=outline_entry.concrete_ending_hook.trigger,
        attempt=outline_entry.objective,
        complication=outline_entry.primary_obstacle,
        price_paid=outline_entry.cost_if_success,
        partial_failure_mode=outline_entry.outcome_type,
        ending_hook_delivery=outline_entry.concrete_ending_hook.next_problem,
        emotional_anchor=outline_entry.emotional_reveal,
        civilian_texture=outline_entry.civilian_life_detail,
        ideology_clash=outline_entry.ideology_pressure,
        primary_interpersonal_conflict=outline_entry.side_character_friction,
        independent_side_character_move=outline_entry.independent_side_character_move
        or f"{support} makes an independent pressure move.",
        genre_specific_focus=outline_entry.chapter_mode,
        genre_specific_beats=[outline_entry.civilian_life_detail, outline_entry.ideology_pressure],
        story_turn=ChapterStoryTurn(
            irreversible_change=outline_entry.ending_state,
            protagonist_choice=outline_entry.character_turn,
            choice_alternatives=[outline_entry.primary_obstacle, outline_entry.cost_if_success],
            permanent_consequence=outline_entry.cost_if_success,
            why_this_chapter_cannot_be_cut=outline_entry.reveal,
            state_before=outline_entry.objective,
            state_after=outline_entry.ending_state,
        ),
    )


def _fallback_chapter_critique(chapter: Any, lint_result: ChapterLintResult) -> ChapterCritique:
    findings = lint_result.combined_findings()
    return ChapterCritique(
        strengths=["Checkpoint fallback critique used local lint signals."],
        warnings=findings,
        revision_required=bool(findings),
        focus=findings[:3],
        ending_hook_type="unknown",
        forward_motion_score=7 if not findings else 5,
        ending_concreteness_score=7 if not findings else 5,
        scene_turn_resolution_score=7 if not findings else 5,
        cost_consequence_realism_score=7 if not findings else 5,
        side_character_independence_score=7,
        proper_noun_continuity_score=8,
        repetition_risk_score=0 if not findings else 5,
        emotional_depth_score=7,
        ideology_clarity_score=7,
        civilian_texture_score=7,
        blocking_issues=findings[:3],
        soft_warnings=findings[3:],
        repair_scope="targeted_scene_and_ending" if findings else "none",
    )


def _fallback_chapter_summary(chapter: Any, outline_entry: StructuredOutlineEntry, plan: ChapterPlan) -> str:
    prose_words = (chapter.content or "").split()
    excerpt = " ".join(prose_words[:80]).strip()
    if excerpt:
        return (
            f"Chapter {chapter.chapter_number} follows {outline_entry.objective}. "
            f"The planned turn is {plan.story_turn.irreversible_change or outline_entry.ending_state}. "
            f"Draft excerpt: {excerpt}"
        )
    return (
        f"Chapter {chapter.chapter_number} follows {outline_entry.objective} and ends with "
        f"{outline_entry.ending_state}."
    )


def _fallback_continuity_update(
    chapter: Any,
    ledger: ContinuityLedger,
    outline_entry: StructuredOutlineEntry,
    plan: ChapterPlan,
) -> ChapterContinuityUpdate:
    timeline_entry = f"Chapter {chapter.chapter_number}: {chapter.summary or outline_entry.ending_state}"
    return ChapterContinuityUpdate(
        chapter_outcome=outline_entry.ending_state,
        current_patch_status=ledger.current_patch_status,
        character_states=dict(ledger.character_states),
        world_state=ledger.world_state or outline_entry.ending_state,
        open_threads=_dedupe([*ledger.open_threads, outline_entry.concrete_ending_hook.next_problem]),
        resolved_threads=list(ledger.resolved_threads),
        timeline_entry=timeline_entry,
        timeline=_dedupe([*ledger.timeline, timeline_entry]),
        new_entities_introduced=[],
        entity_state_changes={},
        open_promises_by_name=dict(ledger.open_promises_by_name),
        ideology_state_by_character=dict(ledger.ideology_state_by_character),
        ideology_shift_notes={},
        memory_damage=dict(ledger.memory_damage),
        trust_fractures=dict(ledger.trust_fractures),
        civilian_pressure_points=_dedupe([*ledger.civilian_pressure_points, outline_entry.civilian_life_detail]),
        emotional_open_loops=dict(ledger.emotional_open_loops),
        side_character_decisions=dict(ledger.side_character_decisions),
        story_turn=plan.story_turn,
        genre_state=dict(ledger.genre_state),
        system_state_transitions=[],
    )


def _validated_outline_or_fallback(
    session: Session,
    project: Any,
    run: GenerationRun,
    story_bible: StoryBible,
    outline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        return parse_outline(json.dumps({"chapters": outline}), run.requested_chapters)
    except Exception as exc:
        record_event(
            session,
            run,
            "outline_validation_fallback",
            {
                "message": "Combined outline failed full-book validation; generated a deterministic outline.",
                "error": str(exc),
            },
        )
        return parse_outline(
            json.dumps(
                {
                    "chapters": _fallback_outline_entries(
                        project,
                        story_bible,
                        1,
                        run.requested_chapters,
                        run.requested_chapters,
                    )
                }
            ),
            run.requested_chapters,
        )


def _generate_story_bible(session: Session, run: GenerationRun, settings: Settings, client: ProviderManager | OllamaClient) -> StoryBible:
    project = run.project
    provider_name, model_name = _resolve_stage_route(client, run, "story_bible")
    run.current_step = "story_bible"
    record_event(
        session,
        run,
        "story_bible_started",
        {"message": "Generating story bible.", "provider_name": provider_name, "model_name": model_name},
    )
    session.commit()
    try:
        story_bible = _generate_structured_output(
            session,
            run,
            client,
            provider_name,
            model_name,
            lambda: build_story_bible_messages(project, run),
            parse_story_bible,
            "story bible",
            "story_bible",
        )
    except Exception as exc:
        story_bible = _fallback_story_bible(project, run)
        record_event(
            session,
            run,
            "story_bible_fallback",
            {
                "message": "Story bible model output was unusable after repair; generated a deterministic story bible.",
                "error": str(exc),
            },
        )
    selected_profile = genre_profile((project.story_brief or {}).get("genre_profile"))
    story_bible = story_bible.model_copy(
        update={
            "genre_profile": selected_profile.id,
            "genre_contract": story_bible.genre_contract or list(selected_profile.genre_contract),
        }
    )
    approved_canon = _project_approved_canon(project)
    if approved_canon:
        story_bible = StoryBible.model_validate(
            {
                **story_bible.model_dump(),
                "canon_registry": merge_canonical_entities(approved_canon, story_bible.canon_registry),
            }
        )
    run.story_bible = story_bible.model_dump()
    run.continuity_ledger = _build_initial_ledger(story_bible).model_dump()
    record_event(session, run, "story_bible_completed", {"logline": story_bible.logline})
    session.commit()
    return story_bible


def _generate_outline_chunks(
    session: Session,
    run: GenerationRun,
    story_bible: StoryBible,
    client: ProviderManager | OllamaClient,
    provider_name: str,
    model_name: str,
) -> list[dict[str, Any]]:
    project = run.project
    outline: list[dict[str, Any]] = []
    for start_chapter in range(1, run.requested_chapters + 1, OUTLINE_CHUNK_SIZE):
        end_chapter = min(run.requested_chapters, start_chapter + OUTLINE_CHUNK_SIZE - 1)
        record_event(
            session,
            run,
            "outline_chunk_started",
            {
                "message": f"Generating outline chapters {start_chapter}-{end_chapter}.",
                "start_chapter": start_chapter,
                "end_chapter": end_chapter,
                "provider_name": provider_name,
                "model_name": model_name,
            },
        )
        session.commit()
        try:
            chunk = _generate_structured_output(
                session,
                run,
                client,
                provider_name,
                model_name,
                lambda start=start_chapter, end=end_chapter, prior=list(outline): build_outline_chunk_messages(
                    project,
                    run,
                    story_bible,
                    start_chapter=start,
                    end_chapter=end,
                    prior_outline=prior,
                ),
                lambda raw, start=start_chapter, end=end_chapter: parse_outline_chunk(raw, start, end),
                f"structured outline chapters {start_chapter}-{end_chapter}",
                "outline_chunk",
                None,
            )
        except Exception as exc:
            chunk = _fallback_outline_entries(
                project,
                story_bible,
                start_chapter,
                end_chapter,
                run.requested_chapters,
            )
            record_event(
                session,
                run,
                "outline_chunk_fallback",
                {
                    "message": f"Outline chunk {start_chapter}-{end_chapter} was unusable after repair; generated deterministic entries.",
                    "error": str(exc),
                    "start_chapter": start_chapter,
                    "end_chapter": end_chapter,
                },
            )
        outline.extend(chunk)
        record_event(
            session,
            run,
            "outline_chunk_completed",
            {
                "message": f"Accepted outline chapters {start_chapter}-{end_chapter}.",
                "start_chapter": start_chapter,
                "end_chapter": end_chapter,
                "chapters": len(chunk),
            },
        )
        session.commit()
    return outline


def _generate_outline(session: Session, run: GenerationRun, story_bible: StoryBible, client: ProviderManager | OllamaClient) -> None:
    project = run.project
    provider_name, model_name = _resolve_stage_route(client, run, "outline")
    run.current_step = "outline"
    record_event(
        session,
        run,
        "outline_started",
        {"message": "Generating structured outline.", "provider_name": provider_name, "model_name": model_name},
    )
    session.commit()
    try:
        if run.requested_chapters > OUTLINE_CHUNK_THRESHOLD:
            outline = _generate_outline_chunks(session, run, story_bible, client, provider_name, model_name)
        else:
            outline = _generate_structured_output(
                session,
                run,
                client,
                provider_name,
                model_name,
                lambda: build_outline_messages(project, run, story_bible),
                lambda raw: parse_outline(raw, run.requested_chapters),
                "structured outline",
                "outline",
            )
        run.outline = _validated_outline_or_fallback(session, project, run, story_bible, outline)
    except Exception as exc:
        run.outline = _fallback_outline_entries(project, story_bible, 1, run.requested_chapters, run.requested_chapters)
        record_event(
            session,
            run,
            "outline_fallback",
            {
                "message": "Outline model output was unusable after repair; generated a deterministic outline.",
                "error": str(exc),
            },
        )
    create_chapters_from_outline(session, run)
    record_event(session, run, "outline_completed", {"chapters": len(run.outline or [])})
    session.commit()


def _pause_for_outline_review(session: Session, run: GenerationRun) -> None:
    run.status = RunStatus.AWAITING_APPROVAL
    run.current_step = "outline_review"
    run.current_chapter = None
    record_event(session, run, "outline_ready_for_approval", {"message": "Outline is ready for approval."})
    session.commit()


def _draft_chapter(
    session: Session,
    run: GenerationRun,
    chapter: Any,
    outline_entry: StructuredOutlineEntry,
    story_bible: StoryBible,
    settings: Settings,
    client: ProviderManager | OllamaClient,
) -> None:
    project = run.project
    ledger = _continuity_ledger_from_run(run)
    prior_chapters = _chapter_prior_context(run, chapter.chapter_number)
    _sync_summary_context(run, settings.chapter_summary_window)
    run.current_chapter = chapter.chapter_number

    plan = _checkpointed_plan(chapter)
    if plan is None:
        plan_provider_name, plan_model_name = _resolve_stage_route(client, run, "chapter_plan")
        run.current_step = "chapter_plan"
        record_event(
            session,
            run,
            "chapter_planning",
            {
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "provider_name": plan_provider_name,
                "model_name": plan_model_name,
            },
        )
        session.commit()

        try:
            plan = _generate_structured_output(
                session,
                run,
                client,
                plan_provider_name,
                plan_model_name,
                lambda: build_chapter_plan_messages(
                    project,
                    run,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    run.summary_context or "",
                ),
                parse_chapter_plan,
                f"chapter {chapter.chapter_number} plan",
                "chapter_plan",
                chapter.chapter_number,
            )
        except Exception as exc:
            plan = _fallback_chapter_plan(chapter, outline_entry, story_bible)
            record_event(
                session,
                run,
                "chapter_plan_fallback",
                {
                    "message": f"Chapter {chapter.chapter_number} plan used deterministic fallback.",
                    "chapter_number": chapter.chapter_number,
                    "error": str(exc),
                },
            )
        _persist_structured_plan(chapter, plan)
        session.commit()
    else:
        record_event(
            session,
            run,
            "chapter_plan_checkpoint_reused",
            {"message": f"Reused saved plan for chapter {chapter.chapter_number}.", "chapter_number": chapter.chapter_number},
        )
        session.commit()

    _ensure_not_canceled(session, run)
    if not (chapter.content or "").strip():
        draft_provider_name, draft_model_name = _resolve_stage_route(client, run, "chapter_draft")
        run.current_step = "chapter_draft"
        record_event(
            session,
            run,
            "chapter_drafting",
            {
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "provider_name": draft_provider_name,
                "model_name": draft_model_name,
            },
        )
        session.commit()

        chapter.content = sanitize_chapter_content(
            _supervised_provider_chat(
                session,
                run,
                client,
                draft_provider_name,
                draft_model_name,
                build_chapter_draft_messages(
                    project,
                    run,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    run.summary_context or "",
                    plan,
                ),
                stage="chapter_draft",
                chapter_number=chapter.chapter_number,
                metadata={"label": f"chapter {chapter.chapter_number} draft"},
            )
        )
        if not chapter.content.strip():
            raise RuntimeError(f"Chapter {chapter.chapter_number} draft was empty.")
        chapter.word_count = len((chapter.content or "").split())
        session.commit()
    else:
        chapter.word_count = len((chapter.content or "").split())
        record_event(
            session,
            run,
            "chapter_draft_checkpoint_reused",
            {"message": f"Reused saved draft for chapter {chapter.chapter_number}.", "chapter_number": chapter.chapter_number},
        )
        session.commit()

    _ensure_not_canceled(session, run)
    run.current_step = "chapter_revision"
    lint_result = lint_chapter(chapter, outline_entry, plan, story_bible, ledger, prior_chapters)
    combined_critique = _checkpointed_critique(chapter)
    critique_provider_name, critique_model_name = _resolve_stage_route(client, run, "chapter_critique")
    if combined_critique is None:
        try:
            critique = _generate_structured_output(
                session,
                run,
                client,
                critique_provider_name,
                critique_model_name,
                lambda: build_chapter_critique_messages(
                    project,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    plan,
                    lint_result.combined_findings(),
                ),
                parse_chapter_critique,
                f"chapter {chapter.chapter_number} critique",
                "chapter_critique",
                chapter.chapter_number,
            )
        except Exception as exc:
            critique = _fallback_chapter_critique(chapter, lint_result)
            record_event(
                session,
                run,
                "chapter_critique_fallback",
                {
                    "message": f"Chapter {chapter.chapter_number} critique used local lint fallback.",
                    "chapter_number": chapter.chapter_number,
                    "error": str(exc),
                },
            )
        combined_critique = _apply_quality_profile_to_critique(
            run,
            _combine_chapter_feedback(critique, lint_result),
        )
        _persist_structured_qa(chapter, combined_critique)
        session.commit()
    else:
        record_event(
            session,
            run,
            "chapter_critique_checkpoint_reused",
            {
                "message": f"Reused saved critique for chapter {chapter.chapter_number}.",
                "chapter_number": chapter.chapter_number,
            },
        )
        session.commit()

    if combined_critique.revision_required:
        record_event(
            session,
            run,
            "chapter_revision_started",
            {
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "repair_scope": combined_critique.repair_scope,
                "provider_name": critique_provider_name,
                "model_name": critique_model_name,
            },
        )
        session.commit()
        revision_provider_name, revision_model_name = _resolve_stage_route(client, run, "chapter_revision")
        chapter.content = sanitize_chapter_content(
            _supervised_provider_chat(
                session,
                run,
                client,
                revision_provider_name,
                revision_model_name,
                build_chapter_revision_messages(
                    project,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    plan,
                    combined_critique,
                    combined_critique.blocking_issues + combined_critique.soft_warnings,
                ),
                stage="chapter_revision",
                chapter_number=chapter.chapter_number,
                metadata={
                    "label": f"chapter {chapter.chapter_number} revision",
                    "repair_scope": combined_critique.repair_scope,
                },
            )
        )
        if not chapter.content.strip():
            raise RuntimeError(f"Chapter {chapter.chapter_number} revision was empty.")
        chapter.word_count = len((chapter.content or "").split())
        session.commit()

        final_lint = lint_chapter(chapter, outline_entry, plan, story_bible, ledger, prior_chapters)
        try:
            final_critique = _generate_structured_output(
                session,
                run,
                client,
                critique_provider_name,
                critique_model_name,
                lambda: build_chapter_critique_messages(
                    project,
                    chapter,
                    outline_entry,
                    story_bible,
                    ledger,
                    plan,
                    final_lint.combined_findings(),
                ),
                parse_chapter_critique,
                f"chapter {chapter.chapter_number} post-repair critique",
                "chapter_critique",
                chapter.chapter_number,
            )
        except Exception as exc:
            final_critique = _fallback_chapter_critique(chapter, final_lint)
            record_event(
                session,
                run,
                "chapter_critique_fallback",
                {
                    "message": f"Chapter {chapter.chapter_number} post-repair critique used local lint fallback.",
                    "chapter_number": chapter.chapter_number,
                    "error": str(exc),
                },
            )
        combined_critique = _apply_quality_profile_to_critique(
            run,
            _combine_chapter_feedback(final_critique, final_lint),
        )
        _persist_structured_qa(chapter, combined_critique)
        session.commit()

    _ensure_not_canceled(session, run)
    if not (chapter.summary or "").strip():
        summary_provider_name, summary_model_name = _resolve_stage_route(client, run, "chapter_summary")
        run.current_step = "chapter_summary"
        try:
            chapter.summary = _supervised_provider_chat(
                session,
                run,
                client,
                summary_provider_name,
                summary_model_name,
                build_summary_messages(chapter, outline_entry),
                stage="chapter_summary",
                chapter_number=chapter.chapter_number,
                metadata={"label": f"chapter {chapter.chapter_number} summary"},
            ).strip()
            if not chapter.summary:
                raise RuntimeError(f"Chapter {chapter.chapter_number} summary was empty.")
        except Exception as exc:
            chapter.summary = _fallback_chapter_summary(chapter, outline_entry, plan)
            record_event(
                session,
                run,
                "chapter_summary_fallback",
                {
                    "message": f"Chapter {chapter.chapter_number} summary used deterministic fallback.",
                    "chapter_number": chapter.chapter_number,
                    "error": str(exc),
                },
            )
        session.commit()

    if chapter.continuity_update:
        continuity_update = ChapterContinuityUpdate.model_validate(chapter.continuity_update)
        record_event(
            session,
            run,
            "continuity_checkpoint_reused",
            {
                "message": f"Reused saved continuity update for chapter {chapter.chapter_number}.",
                "chapter_number": chapter.chapter_number,
            },
        )
    else:
        continuity_provider_name, continuity_model_name = _resolve_stage_route(client, run, "continuity_update")
        run.current_step = "continuity_update"
        try:
            continuity_update = _generate_structured_output(
                session,
                run,
                client,
                continuity_provider_name,
                continuity_model_name,
                lambda: build_continuity_update_messages(project, chapter, ledger, story_bible),
                parse_continuity_update,
                f"chapter {chapter.chapter_number} continuity update",
                "continuity_update",
                chapter.chapter_number,
            )
        except Exception as exc:
            continuity_update = _fallback_continuity_update(chapter, ledger, outline_entry, plan)
            record_event(
                session,
                run,
                "continuity_update_fallback",
                {
                    "message": f"Chapter {chapter.chapter_number} continuity used deterministic fallback.",
                    "chapter_number": chapter.chapter_number,
                    "error": str(exc),
                },
            )
    ledger_after = _ledger_from_update(ledger, continuity_update)
    ledger_after = _ledger_with_chapter_mode(ledger_after, chapter.chapter_number, outline_entry.chapter_mode)
    if continuity_update.timeline != ledger_after.timeline:
        continuity_update.timeline = ledger_after.timeline
    _apply_continuity_canon_warnings(chapter, continuity_update)
    chapter.continuity_update = continuity_update.model_dump()
    chapter.status = ChapterStatus.COMPLETED
    chapter.error_message = None
    run.continuity_ledger = ledger_after.model_dump()
    _sync_summary_context(run, settings.chapter_summary_window)
    record_event(
        session,
        run,
        "chapter_completed",
        {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "word_count": chapter.word_count,
        },
    )
    session.commit()


def _run_manuscript_qa(
    session: Session,
    run: GenerationRun,
    chapters: list,
    client: ProviderManager | OllamaClient,
) -> tuple[ManuscriptQaReport, str]:
    story_bible = _story_bible_from_run(run)
    lint_findings = lint_manuscript(chapters)
    deterministic_notes = manuscript_quality_notes(chapters, story_bible)
    provider_name, model_name = _resolve_stage_route(client, run, "manuscript_qa")
    run.current_step = "manuscript_qa"
    record_event(
        session,
        run,
        "manuscript_qa_started",
        {"message": "Running manuscript QA.", "provider_name": provider_name, "model_name": model_name},
    )
    session.commit()
    try:
        qa_report = _generate_structured_output(
            session,
            run,
            client,
            provider_name,
            model_name,
            lambda: build_manuscript_qa_messages(run.project, story_bible, lint_findings, chapters),
            parse_manuscript_qa_report,
            "manuscript QA report",
            "manuscript_qa",
        )
    except Exception as exc:
        fallback_message = (
            "Manuscript QA model output was unusable after repair; generated a deterministic QA report instead."
        )
        record_event(
            session,
            run,
            "manuscript_qa_fallback",
            {"message": fallback_message, "error": str(exc)},
        )
        session.commit()
        qa_report = ManuscriptQaReport(
            overall_verdict=(
                "Manuscript QA completed with deterministic lint and chapter QA signals because the model QA "
                "response was unusable."
            ),
            warnings=[fallback_message],
        )
    qa_report = qa_report.model_copy(
        update={
            "lint_findings": _dedupe([*qa_report.lint_findings, *lint_findings]),
            "chapter_ending_quality_notes": _dedupe(
                [*qa_report.chapter_ending_quality_notes, *deterministic_notes["chapter_ending_quality_notes"]]
            ),
            "easy_win_warnings": _dedupe([*qa_report.easy_win_warnings, *deterministic_notes["easy_win_warnings"]]),
            "proper_noun_continuity_findings": _dedupe(
                [*qa_report.proper_noun_continuity_findings, *deterministic_notes["proper_noun_continuity_findings"]]
            ),
            "side_character_agency_notes": _dedupe(
                [*qa_report.side_character_agency_notes, *deterministic_notes["side_character_agency_notes"]]
            ),
            "atmospheric_repetition_findings": _dedupe(
                [*qa_report.atmospheric_repetition_findings, *deterministic_notes["atmospheric_repetition_findings"]]
            ),
            "emotional_pacing_notes": _dedupe(
                [*qa_report.emotional_pacing_notes, *deterministic_notes["emotional_pacing_notes"]]
            ),
            "ideology_consistency_findings": _dedupe(
                [*qa_report.ideology_consistency_findings, *deterministic_notes["ideology_consistency_findings"]]
            ),
            "civilian_texture_findings": _dedupe(
                [*qa_report.civilian_texture_findings, *deterministic_notes["civilian_texture_findings"]]
            ),
            "technical_escalation_fatigue_findings": _dedupe(
                [
                    *qa_report.technical_escalation_fatigue_findings,
                    *deterministic_notes["technical_escalation_fatigue_findings"],
                ]
            ),
            "crisis_loop_findings": _dedupe(
                [
                    *qa_report.crisis_loop_findings,
                    *deterministic_notes["crisis_loop_findings"],
                ]
            ),
            "scene_mode_distribution_notes": _dedupe(
                [
                    *qa_report.scene_mode_distribution_notes,
                    *deterministic_notes["scene_mode_distribution_notes"],
                ]
            ),
            "story_turn_quality_notes": _dedupe(
                [
                    *qa_report.story_turn_quality_notes,
                    *deterministic_notes["story_turn_quality_notes"],
                ]
            ),
            "genre_contract_notes": _dedupe(
                [*qa_report.genre_contract_notes, *deterministic_notes["genre_contract_notes"]]
            ),
            "continuity_bible_findings": _dedupe(
                [*qa_report.continuity_bible_findings, *deterministic_notes["continuity_bible_findings"]]
            ),
            "continuity_bible_table": _dedupe_continuity_bible_table(
                [*qa_report.continuity_bible_table, *deterministic_notes["continuity_bible_table"]]
            ),
        }
    )
    qa_markdown = render_qa_report_markdown(qa_report)
    return qa_report, qa_markdown


def _fallback_developmental_rewrite_plan(
    chapters: list,
    qa_report: ManuscriptQaReport,
) -> DevelopmentalRewritePlan:
    pre_rewrite_risks = _dedupe(
        [
            *qa_report.warnings,
            *qa_report.continuity_risks,
            *qa_report.repetition_risks,
            *qa_report.chapter_ending_quality_notes,
            *qa_report.technical_escalation_fatigue_findings,
            *qa_report.crisis_loop_findings,
            *qa_report.scene_mode_distribution_notes,
            *qa_report.story_turn_quality_notes,
            *qa_report.continuity_bible_findings,
        ]
    )
    chapter_actions = []
    for chapter in chapters:
        qa_notes = chapter.qa_notes or {}
        cuttable_risk = int(qa_notes.get("cuttable_chapter_risk_score") or 0)
        repetition_risk = int(qa_notes.get("repetition_risk_score") or 0)
        revision_required = bool(qa_notes.get("revision_required"))
        action = "rewrite" if revision_required or cuttable_risk >= 6 or repetition_risk >= 7 else "keep"
        reason = "Chapter-level QA indicates structural risk." if action == "rewrite" else "Chapter has a stored story turn and no high deterministic risk."
        story_turn = (chapter.continuity_update or {}).get("story_turn", {}) if isinstance(chapter.continuity_update, dict) else {}
        chapter_actions.append(
            {
                "chapter_numbers": [chapter.chapter_number],
                "action": action,
                "reason": reason,
                "required_story_change": story_turn.get("why_this_chapter_cannot_be_cut") or chapter.outline_summary,
                "permanent_consequence": story_turn.get("permanent_consequence") or "Preserve a concrete before/after state change.",
            }
        )
    return DevelopmentalRewritePlan(
        overall_diagnosis=(
            "Deterministic developmental rewrite plan generated from manuscript QA because the model rewrite plan was unavailable."
        ),
        act_structure_notes=["Review chapter actions against the original act plan before rewriting prose."],
        chapter_actions=chapter_actions,
        pre_rewrite_risks=pre_rewrite_risks,
        post_rewrite_risk_targets=[
            "Every retained or rewritten chapter should carry a distinct irreversible story turn.",
            "Merged or cut chapters should preserve any necessary permanent consequence elsewhere.",
            "A follow-up QA pass should show lower repetition, continuity, and cuttable-chapter risk.",
        ],
    )


def _run_developmental_rewrite(
    session: Session,
    run: GenerationRun,
    chapters: list,
    qa_report: ManuscriptQaReport,
    client: ProviderManager | OllamaClient,
) -> tuple[DevelopmentalRewritePlan, str, str, str]:
    story_bible = _story_bible_from_run(run)
    continuity_ledger = _continuity_ledger_from_run(run)
    provider_name, model_name = _resolve_stage_route(client, run, "developmental_rewrite")
    run.current_step = "developmental_rewrite"
    record_event(
        session,
        run,
        "developmental_rewrite_started",
        {"message": "Planning a developmental rewrite.", "provider_name": provider_name, "model_name": model_name},
    )
    session.commit()
    try:
        plan = _generate_structured_output(
            session,
            run,
            client,
            provider_name,
            model_name,
            lambda: build_developmental_rewrite_messages(run.project, story_bible, continuity_ledger, qa_report, chapters),
            parse_developmental_rewrite_plan,
            "developmental rewrite plan",
            "developmental_rewrite",
        )
    except Exception as exc:
        record_event(
            session,
            run,
            "developmental_rewrite_fallback",
            {"message": "Developmental rewrite model output was unusable; generated a deterministic plan.", "error": str(exc)},
        )
        session.commit()
        plan = _fallback_developmental_rewrite_plan(chapters, qa_report)

    rewrite_markdown = render_developmental_rewrite_report_markdown(plan, qa_report)
    revised_outline_markdown = render_revised_outline_markdown(run.project.title, plan, chapters)
    developmental_qa_markdown = render_developmental_qa_comparison_markdown(plan, qa_report)
    record_event(
        session,
        run,
        "developmental_rewrite_completed",
        {"message": "Developmental rewrite plan created.", "chapter_action_count": len(plan.chapter_actions)},
    )
    session.commit()
    return plan, rewrite_markdown, revised_outline_markdown, developmental_qa_markdown


def _completed_final_edit_chapters(run: GenerationRun) -> set[int]:
    completed: set[int] = set()
    for event in run.events:
        if event.event_type != "final_chapter_edit_completed":
            continue
        chapter_number = event.payload.get("chapter_number")
        try:
            completed.add(int(chapter_number))
        except (TypeError, ValueError):
            continue
    return completed


def _run_final_editing_pass(
    session: Session,
    run: GenerationRun,
    chapters: list,
    qa_report: ManuscriptQaReport,
    developmental_plan: DevelopmentalRewritePlan | None,
    client: ProviderManager | OllamaClient,
) -> None:
    story_bible = _story_bible_from_run(run)
    continuity_ledger = _continuity_ledger_from_run(run)
    provider_name, model_name = _resolve_stage_route(client, run, "chapter_revision")
    edited_chapters = _completed_final_edit_chapters(run)
    run.current_step = "chapter_edit"
    run.current_chapter = None
    record_event(
        session,
        run,
        "final_editing_started",
        {
            "message": "Running final chapter editing pass.",
            "provider_name": provider_name,
            "model_name": model_name,
            "chapters": len(chapters),
        },
    )
    session.commit()

    for chapter in chapters:
        _ensure_not_canceled(session, run)
        run.current_step = "chapter_edit"
        run.current_chapter = chapter.chapter_number
        if chapter.chapter_number in edited_chapters:
            record_event(
                session,
                run,
                "final_chapter_edit_checkpoint_reused",
                {
                    "message": f"Reused saved final edit for chapter {chapter.chapter_number}.",
                    "chapter_number": chapter.chapter_number,
                },
            )
            session.commit()
            continue

        try:
            outline_entry = _outline_entry(run, chapter.chapter_number)
            edited_content = sanitize_chapter_content(
                _supervised_provider_chat(
                    session,
                    run,
                    client,
                    provider_name,
                    model_name,
                    build_chapter_edit_messages(
                        run.project,
                        chapter,
                        outline_entry,
                        story_bible,
                        continuity_ledger,
                        qa_report,
                        developmental_plan,
                    ),
                    stage="chapter_edit",
                    chapter_number=chapter.chapter_number,
                    metadata={"label": f"chapter {chapter.chapter_number} final edit"},
                )
            )
            if not edited_content.strip():
                raise RuntimeError(f"Chapter {chapter.chapter_number} final edit was empty.")
            chapter.content = edited_content
            chapter.word_count = len((chapter.content or "").split())
            record_event(
                session,
                run,
                "final_chapter_edit_completed",
                {
                    "message": f"Final edit saved for chapter {chapter.chapter_number}.",
                    "chapter_number": chapter.chapter_number,
                    "word_count": chapter.word_count,
                },
            )
            session.commit()
        except Exception as exc:
            chapter.word_count = len((chapter.content or "").split())
            record_event(
                session,
                run,
                "final_chapter_edit_fallback",
                {
                    "message": f"Final edit failed for chapter {chapter.chapter_number}; kept the existing chapter prose.",
                    "chapter_number": chapter.chapter_number,
                    "error": str(exc),
                },
            )
            session.commit()

    run.current_chapter = None
    record_event(
        session,
        run,
        "final_editing_completed",
        {"message": "Final chapter editing pass completed.", "chapters": len(chapters)},
    )
    session.commit()


def process_run(session: Session, run: GenerationRun, settings: Settings, client: ProviderManager | OllamaClient) -> None:
    _ensure_not_canceled(session, run)

    story_bible = _story_bible_from_run(run) if run.story_bible else _generate_story_bible(session, run, settings, client)

    if not run.outline:
        _generate_outline(session, run, story_bible, client)
        if run.pause_after_outline:
            _pause_for_outline_review(session, run)
            return
    else:
        create_chapters_from_outline(session, run)
        session.commit()

    run.continuity_ledger = _build_initial_ledger(story_bible).model_dump()
    _sync_summary_context(run, settings.chapter_summary_window)
    session.commit()

    chapters = _require_requested_chapters(session, run)
    for chapter in chapters:
        if chapter.status == ChapterStatus.COMPLETED and chapter.content and chapter.summary and chapter.continuity_update:
            ledger = _continuity_ledger_from_run(run)
            ledger_after = _ledger_from_update(ledger, ChapterContinuityUpdate.model_validate(chapter.continuity_update))
            outline_entry = _outline_entry(run, chapter.chapter_number)
            ledger_after = _ledger_with_chapter_mode(ledger_after, chapter.chapter_number, outline_entry.chapter_mode)
            run.continuity_ledger = ledger_after.model_dump()
            _sync_summary_context(run, settings.chapter_summary_window)
            session.commit()
            continue

        _ensure_not_canceled(session, run)
        outline_entry = _outline_entry(run, chapter.chapter_number)
        _draft_chapter(session, run, chapter, outline_entry, story_bible, settings, client)

    completed_chapters = _require_requested_chapters(session, run)
    incomplete_chapters = [
        chapter.chapter_number
        for chapter in completed_chapters
        if chapter.status != ChapterStatus.COMPLETED
        or not chapter.content
        or not chapter.summary
        or not chapter.continuity_update
    ]
    if incomplete_chapters:
        raise RuntimeError(
            "Run finished without fully drafted chapters for: "
            + ", ".join(str(chapter_number) for chapter_number in incomplete_chapters)
        )

    qa_report, qa_markdown = _run_manuscript_qa(session, run, completed_chapters, client)
    developmental_plan: DevelopmentalRewritePlan | None = None
    rewrite_markdown = None
    revised_outline_markdown = None
    developmental_qa_markdown = None
    if run.developmental_rewrite_enabled:
        developmental_plan, rewrite_markdown, revised_outline_markdown, developmental_qa_markdown = _run_developmental_rewrite(
            session,
            run,
            completed_chapters,
            qa_report,
            client,
        )

    _run_final_editing_pass(session, run, completed_chapters, qa_report, developmental_plan, client)
    completed_chapters = _require_requested_chapters(session, run)
    qa_report, qa_markdown = _run_manuscript_qa(session, run, completed_chapters, client)

    run.current_step = "export"
    record_event(session, run, "artifact_export_started", {"message": "Rendering manuscript artifacts."})
    session.commit()

    artifacts = export_run_artifacts(
        settings.artifacts_dir,
        run.project,
        run,
        completed_chapters,
        qa_markdown,
        rewrite_markdown,
        revised_outline_markdown,
        developmental_qa_markdown,
    )
    replace_artifacts(session, run, artifacts)
    run.current_step = "completed"
    run.current_chapter = None
    run.status = RunStatus.COMPLETED
    run.worker_id = None
    run.completed_at = datetime.utcnow()
    record_event(
        session,
        run,
        "run_completed",
        {"message": "Run finished successfully.", "artifact_count": len(artifacts)},
    )
    session.commit()


def process_run_safe(session: Session, run: GenerationRun, settings: Settings, client: ProviderManager | OllamaClient) -> None:
    try:
        process_run(session, run, settings, client)
    except RunCanceled:
        return
    except (ProviderError, ProviderTransportError) as exc:
        if run.current_chapter:
            chapter = next((item for item in run.chapters if item.chapter_number == run.current_chapter), None)
            if chapter is not None:
                chapter.status = ChapterStatus.FAILED
                chapter.error_message = str(exc)
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        run.worker_id = None
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_failed", {"message": str(exc)})
        session.commit()
    except Exception as exc:
        if run.current_chapter:
            chapter = next((item for item in run.chapters if item.chapter_number == run.current_chapter), None)
            if chapter is not None:
                chapter.status = ChapterStatus.FAILED
                chapter.error_message = str(exc)
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        run.worker_id = None
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_failed", {"message": str(exc)})
        session.commit()
