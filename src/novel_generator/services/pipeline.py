from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import ChapterStatus, GenerationRun, RunStatus
from ..repositories import create_chapters_from_outline, record_event, replace_artifacts
from ..schemas import (
    ChapterContinuityUpdate,
    ChapterCritique,
    ChapterPlan,
    ContinuityLedger,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
)
from ..settings import Settings
from .editorial import (
    ChapterLintResult,
    detect_canonical_entity_collisions,
    lint_chapter,
    lint_manuscript,
    manuscript_quality_notes,
    merge_canonical_entities,
    render_qa_report_markdown,
)
from .exports import export_run_artifacts
from .ollama import OllamaClient
from .provider_errors import ProviderError, ProviderTransportError
from .prompts import (
    build_chapter_critique_messages,
    build_chapter_draft_messages,
    build_chapter_plan_messages,
    build_chapter_revision_messages,
    build_continuity_update_messages,
    build_json_repair_messages,
    build_manuscript_qa_messages,
    build_outline_messages,
    build_story_bible_messages,
    build_summary_messages,
    parse_chapter_critique,
    parse_chapter_plan,
    parse_continuity_update,
    parse_manuscript_qa_report,
    parse_outline,
    parse_story_bible,
    rolling_context,
    sanitize_chapter_content,
)
from .providers import ProviderManager


class RunCanceled(Exception):
    pass


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


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


def _ensure_not_canceled(session: Session, run: GenerationRun) -> None:
    session.refresh(run)
    if run.cancel_requested:
        run.status = RunStatus.CANCELED
        run.current_step = "canceled"
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_canceled", {"message": "Cancellation was requested."})
        session.commit()
        raise RunCanceled("Run canceled.")


def _generate_structured_output(
    client: ProviderManager | OllamaClient,
    provider_name: str,
    model_name: str,
    build_messages: Callable[[], list[dict[str, str]]],
    parser: Callable[[str], Any],
    label: str,
) -> Any:
    raw_output = _provider_chat(client, provider_name, model_name, build_messages())
    try:
        return parser(raw_output)
    except Exception:
        repaired_output = _provider_chat(client, provider_name, model_name, build_json_repair_messages(raw_output, label))
        return parser(repaired_output)


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
    )


def _persist_structured_plan(chapter: Any, plan: ChapterPlan) -> None:
    chapter.plan = json.dumps(plan.model_dump(), indent=2)


def _persist_structured_qa(chapter: Any, critique: ChapterCritique) -> None:
    chapter.qa_notes = critique.model_dump()


def _resolve_repair_scope(*scopes: str) -> str:
    if "full_chapter" in scopes:
        return "full_chapter"
    if "targeted_scene_and_ending" in scopes:
        return "targeted_scene_and_ending"
    return "none"


def _combine_chapter_feedback(critique: ChapterCritique, lint_result: ChapterLintResult) -> ChapterCritique:
    blocking_issues = _dedupe([*critique.blocking_issues, *lint_result.blocking_issues])
    soft_warnings = _dedupe([*critique.soft_warnings, *lint_result.soft_warnings])
    warnings = _dedupe([*critique.warnings, *soft_warnings])
    focus = _dedupe([*critique.focus, *blocking_issues[:2], *soft_warnings[:2]])
    revision_required = critique.revision_required or lint_result.needs_repair or bool(blocking_issues)
    repair_scope = _resolve_repair_scope(critique.repair_scope, lint_result.repair_scope)
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


def _chapter_prior_context(run: GenerationRun, chapter_number: int) -> list:
    return [
        prior
        for prior in _sorted_chapters(run)
        if prior.chapter_number < chapter_number and (prior.content or prior.summary)
    ]


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
    story_bible = _generate_structured_output(
        client,
        provider_name,
        model_name,
        lambda: build_story_bible_messages(project, run),
        parse_story_bible,
        "story bible",
    )
    run.story_bible = story_bible.model_dump()
    run.continuity_ledger = _build_initial_ledger(story_bible).model_dump()
    record_event(session, run, "story_bible_completed", {"logline": story_bible.logline})
    session.commit()
    return story_bible


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
    run.outline = _generate_structured_output(
        client,
        provider_name,
        model_name,
        lambda: build_outline_messages(project, run, story_bible),
        lambda raw: parse_outline(raw, run.requested_chapters),
        "structured outline",
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
    plan_provider_name, plan_model_name = _resolve_stage_route(client, run, "chapter_plan")
    run.current_chapter = chapter.chapter_number
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

    plan = _generate_structured_output(
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
    )
    _persist_structured_plan(chapter, plan)
    session.commit()

    _ensure_not_canceled(session, run)
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
        _provider_chat(
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
        )
    )
    if not chapter.content.strip():
        raise RuntimeError(f"Chapter {chapter.chapter_number} draft was empty.")
    chapter.word_count = len((chapter.content or "").split())
    session.commit()

    _ensure_not_canceled(session, run)
    critique_provider_name, critique_model_name = _resolve_stage_route(client, run, "chapter_critique")
    run.current_step = "chapter_revision"
    lint_result = lint_chapter(chapter, outline_entry, plan, story_bible, ledger, prior_chapters)
    critique = _generate_structured_output(
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
    )
    combined_critique = _combine_chapter_feedback(critique, lint_result)
    _persist_structured_qa(chapter, combined_critique)
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
            _provider_chat(
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
            )
        )
        if not chapter.content.strip():
            raise RuntimeError(f"Chapter {chapter.chapter_number} revision was empty.")
        chapter.word_count = len((chapter.content or "").split())
        session.commit()

        final_lint = lint_chapter(chapter, outline_entry, plan, story_bible, ledger, prior_chapters)
        final_critique = _generate_structured_output(
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
        )
        combined_critique = _combine_chapter_feedback(final_critique, final_lint)
        _persist_structured_qa(chapter, combined_critique)
        session.commit()

    _ensure_not_canceled(session, run)
    summary_provider_name, summary_model_name = _resolve_stage_route(client, run, "chapter_summary")
    run.current_step = "chapter_summary"
    chapter.summary = _provider_chat(
        client,
        summary_provider_name,
        summary_model_name,
        build_summary_messages(chapter, outline_entry),
    ).strip()
    if not chapter.summary:
        raise RuntimeError(f"Chapter {chapter.chapter_number} summary was empty.")

    continuity_provider_name, continuity_model_name = _resolve_stage_route(client, run, "continuity_update")
    continuity_update = _generate_structured_output(
        client,
        continuity_provider_name,
        continuity_model_name,
        lambda: build_continuity_update_messages(project, chapter, ledger, story_bible),
        parse_continuity_update,
        f"chapter {chapter.chapter_number} continuity update",
    )
    ledger_after = _ledger_from_update(ledger, continuity_update)
    if continuity_update.timeline != ledger_after.timeline:
        continuity_update.timeline = ledger_after.timeline
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
    qa_report = _generate_structured_output(
        client,
        provider_name,
        model_name,
        lambda: build_manuscript_qa_messages(run.project, story_bible, lint_findings, chapters),
        parse_manuscript_qa_report,
        "manuscript QA report",
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
        }
    )
    qa_markdown = render_qa_report_markdown(qa_report)
    return qa_report, qa_markdown


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

    _, qa_markdown = _run_manuscript_qa(session, run, completed_chapters, client)

    run.current_step = "export"
    record_event(session, run, "artifact_export_started", {"message": "Rendering manuscript artifacts."})
    session.commit()

    artifacts = export_run_artifacts(settings.artifacts_dir, run.project, run, completed_chapters, qa_markdown)
    replace_artifacts(session, run, artifacts)
    run.current_step = "completed"
    run.current_chapter = None
    run.status = RunStatus.COMPLETED
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
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        record_event(session, run, "run_failed", {"message": str(exc)})
        session.commit()
