"""Run a real Ollama manuscript through the worker stack in a fresh, isolated database."""
from __future__ import annotations

import argparse
import json
import os
import time
from sqlalchemy import select
from datetime import datetime, timezone
from pathlib import Path

from ..bootstrap import run_migrations
from ..db import build_session_factory
from ..models import ChapterStatus, GenerationRun, RunStatus
from ..repositories import (
    claim_next_queued_run, create_project, create_run, ensure_provider_configs,
    get_run_for_processing, recover_running_runs,
)
from ..schemas import ProjectCreate, RunCreate, StoryBrief
from ..settings import Settings, get_settings
from .pipeline import process_run_safe
from .providers import ProviderManager
from .runner import install_generation_runtime
from .editorial_coverage import review_coverage
from .autonomous_editorial import ACCEPTANCE_CONTRACT
from .export_integrity import verify_manuscript_exports
from .state import resume_failed_run
from .run_recovery import release_provider_retries
from .worker_lease import exclusive_worker_lease
from .worker_heartbeat import maintain_worker_heartbeat


def load_project_spec(path: Path, model: str) -> ProjectCreate:
    """Reuse the application's story contract while keeping qualification local."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    payload.update(preferred_provider_name="ollama", preferred_model=model, task_routing={})
    project = ProjectCreate.model_validate(payload)
    if not project.requested_chapters * project.min_words_per_chapter <= project.desired_word_count <= project.requested_chapters * project.max_words_per_chapter:
        raise ValueError("Project word target is unreachable within its chapter word ranges.")
    return project


def qualification_evidence(run) -> dict:
    """Summarize persisted observations, including failed and interrupted work."""
    from .run_diagnostics import safe_message

    def utc(value):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    started = utc(getattr(run, "started_at", None))
    completed = utc(getattr(run, "completed_at", None))
    stages = {}
    for attempt in run.stage_attempts:
        route = (attempt.stage, attempt.provider_name, attempt.model_name)
        row = stages.setdefault(route, {
            "stage": route[0], "provider": route[1], "model": route[2],
            "attempts": 0, "status_counts": {}, "recorded_duration_ms": 0,
            "timed_attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "token_metric_attempts": 0,
        })
        row["attempts"] += 1
        status = attempt.status
        row["status_counts"][status] = row["status_counts"].get(status, 0) + 1
        if attempt.duration_ms is not None:
            row["timed_attempts"] += 1
            row["recorded_duration_ms"] += attempt.duration_ms
        metrics = (attempt.attempt_metadata or {}).get("provider_metrics") or {}
        prompt, completion = metrics.get("prompt_eval_count"), metrics.get("eval_count")
        if isinstance(prompt, int) and isinstance(completion, int):
            row["token_metric_attempts"] += 1
            row["prompt_tokens"] += prompt
            row["completion_tokens"] += completion
    events = sorted(run.events, key=lambda event: event.sequence)
    identities = []
    seen = set()
    for event in events:
        if event.event_type != "autonomous_review_completed":
            continue
        for key in ("reviewer_identity", "adjudicator_identity"):
            identity = (event.payload or {}).get(key)
            if isinstance(identity, dict):
                fingerprint = json.dumps(identity, sort_keys=True)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    identities.append(identity)
    failure = next((event.payload for event in reversed(events) if event.event_type == "run_failed"), None)
    counts = {}
    for event in events:
        kind = event.event_type
        if kind.startswith("autonomous_repair_") or kind.startswith("provider_retry_") or kind in {
            "run_requeued", "run_resume_queued", "continuity_output_rejected",
            "continuity_checkpoint_invalidated", "autonomous_metadata_invalidated",
        }:
            counts[kind] = counts.get(kind, 0) + 1
    return {
        "started_at_utc": started.isoformat() if started else None,
        "completed_at_utc": completed.isoformat() if completed else None,
        "wall_seconds": max(0, (completed - started).total_seconds()) if started and completed else None,
        "recovery_count": getattr(run, "recovery_count", 0),
        "event_counts": counts,
        "stages": sorted(stages.values(), key=lambda row: (-row["recorded_duration_ms"], row["stage"], row["model"])),
        "observed_editorial_model_identities": identities,
        "last_failure": None if failure is None else {
            "step": failure.get("failed_step"), "chapter": failure.get("chapter_number"),
            "type": failure.get("error_type"), "message": safe_message(failure.get("message")),
        },
    }


def verification_report(run, artifacts_dir: Path) -> dict:
    from .book_specs import run_book_brief
    brief = run_book_brief(run)
    chapters = sorted(run.chapters, key=lambda chapter: chapter.chapter_number)
    expected = list(range(1, run.requested_chapters + 1))
    complete = (
        run.status == RunStatus.COMPLETED
        and [chapter.chapter_number for chapter in chapters] == expected
        and all(chapter.status == ChapterStatus.COMPLETED and (chapter.content or "").strip()
                and chapter.summary and chapter.continuity_update for chapter in chapters)
    )
    artifact_paths = {artifact.kind: str(artifacts_dir / artifact.relative_path) for artifact in run.artifacts}
    exported = {"markdown", "docx", "qa-report"} <= artifact_paths.keys() and all(
        Path(path).is_file() and Path(path).stat().st_size > 0 for path in artifact_paths.values()
    )
    export_integrity = verify_manuscript_exports(run, chapters, artifact_paths)
    exported = exported and all(item["matches_current_source"] for item in export_integrity.values())
    coverage = review_coverage(run)
    counts = [len((chapter.content or "").split()) for chapter in chapters]
    total = sum(counts)
    total_ok = run.target_word_count > 0 and 0.9 * run.target_word_count <= total <= 1.1 * run.target_word_count
    chapters_ok = bool(expected) and len(counts) == len(expected) and all(
        run.min_words_per_chapter <= count <= run.max_words_per_chapter for count in counts
    )
    return {
        "run_id": run.id,
        "model": run.model_name,
        "status": run.status.value,
        "qualification_evidence": qualification_evidence(run),
        "quality_profile": getattr(run, "quality_profile", "balanced"),
        "book_form": {key: brief.get(key) for key in
                      ("book_type", "content_kind", "structure", "genre_profile", "genre_description")},
        "task_routing": getattr(run, "task_routing", None) or {},
        "autonomous_checks_passed": bool(complete and exported and coverage["accepted"]
            and coverage["acceptance_contract"] >= ACCEPTANCE_CONTRACT and total_ok and chapters_ok),
        "editorial_coverage": {key: coverage[key] for key in (
            "accepted", "acceptance_contract", "chapter_passes", "pair_passes", "pair_target",
            "developmental_passes", "identity_passes", "cross_passes", "cross_target", "whole_book_passed",
        )},
        "current_source_concerns": len(coverage["editorial_concerns"]),
        "error": run.error_message,
        "pipeline_complete": bool(complete and exported),
        "export_integrity": export_integrity,
        "target_words": run.target_word_count,
        "actual_words": total,
        "length_checks": {
            "total_within_target": total_ok,
            "chapters_within_range": chapters_ok,
            "min_words_per_chapter": run.min_words_per_chapter,
            "max_words_per_chapter": run.max_words_per_chapter,
        },
        "chapters": [{"number": chapter.chapter_number, "words": len((chapter.content or "").split()),
                      "qa": chapter.qa_notes} for chapter in chapters],
        "fallback_events": [{"type": event.event_type, "payload": event.payload}
                            for event in run.events if "fallback" in event.event_type],
        "failed_attempts": sum(attempt.status == "failed" for attempt in run.stage_attempts),
        "provider_metrics": [attempt.attempt_metadata.get("provider_metrics", {})
                             for attempt in run.stage_attempts if attempt.attempt_metadata],
        "artifacts": artifact_paths,
        "quality_note": "Automated acceptance is not proof of logical consistency; it records the configured checks and cannot guarantee detection of every narrative defect.",
    }



def outline_verification_report(run) -> dict:
    """An outline qualification is not manuscript acceptance."""
    from .autonomous_editorial import _hash
    from .outline_progression import parse_review
    from .prompts import parse_outline
    expected = list(range(1, run.requested_chapters + 1))
    outline = run.outline or []
    error = run.error_message
    valid = False
    reviewed = False
    if run.story_bible and outline:
        try:
            parse_outline(json.dumps({"chapters": outline}), run.requested_chapters,
                          book_spec=run.story_bible.get("book_spec"))
            valid = True
            if len(outline) == 1:
                reviewed = True  # No cross-chapter progression to review.
            else:
                current_hash = _hash(outline)
                latest = next((e.payload for e in sorted(run.events, key=lambda e: e.sequence, reverse=True)
                               if e.event_type == "outline_progression_review_completed"
                               and e.payload.get("source_outline_hash") == current_hash), None)
                reviewed = bool(latest and parse_review(json.dumps(latest["review"]), outline).passed)
        except (ValueError, KeyError, TypeError) as exc:
            error = error or str(exc)
    chapters = sorted(run.chapters, key=lambda c: c.chapter_number)
    accepted = bool(run.status == RunStatus.AWAITING_APPROVAL and valid and reviewed
                    and [c.chapter_number for c in chapters] == expected
                    and not any(c.content for c in chapters))
    return {
        "run_id": run.id, "model": run.model_name, "status": run.status.value,
        "qualification_mode": "outline-only", "outline_checks_passed": accepted,
        "outline_schema_valid": valid, "progression_passed": reviewed,
        "requested_chapters": run.requested_chapters, "saved_outline_chapters": len(outline),
        "pipeline_complete": False, "autonomous_checks_passed": False, "error": error,
        "failed_attempts": sum(a.status == "failed" for a in run.stage_attempts),
        "quality_note": "Outline qualification only; no manuscript has been accepted or exported.",
    }


def prepare_qualification_directory(output: Path, configuration: dict, *, resume: bool) -> None:
    """Only open an existing isolated qualification directory with a matching manifest."""
    marker = output / "qualification.json"
    if resume:
        if not marker.is_file() or not (output / "verification.db").is_file():
            raise ValueError("Resume requires an existing qualification manifest and verification.db.")
        if json.loads(marker.read_text(encoding="utf-8")) != configuration:
            raise ValueError("Resume settings differ from the saved qualification. Repeat the original options.")
    else:
        output.mkdir(parents=True, exist_ok=False)
        marker.write_text(json.dumps(configuration, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--project-file", type=Path, help="ProjectCreate JSON with your own premise, genre, brief, and word targets; overrides the sample story and size flags.")
    parser.add_argument("--review-model", help="Installed Ollama model for independent editorial review.")
    parser.add_argument("--revision-model", help="Installed Ollama model for automatic revision.")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts") / (
        "local-verification-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")))
    parser.add_argument("--chapters", type=int, default=3)
    parser.add_argument("--words-per-chapter", type=int, default=500)
    parser.add_argument("--context-tokens", type=int, default=32768)
    parser.add_argument("--profile", choices=["draft", "balanced", "strict", "autonomous"], default="autonomous")
    parser.add_argument("--outline-only", action="store_true", help="Qualify the autonomous story bible and outline without drafting.")
    parser.add_argument("--resume", action="store_true", help="Resume the same isolated qualification directory with identical options.")
    args = parser.parse_args()
    if args.chapters < 1 or args.words_per_chapter < 100:
        parser.error("Use at least one chapter and 100 words per chapter.")
    try:
        custom_project = load_project_spec(args.project_file, args.model) if args.project_file else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if args.outline_only and args.profile != "autonomous":
        parser.error("--outline-only requires the autonomous profile.")
    output = args.output_dir.resolve()
    configuration = {
        "version": 1, "model": args.model, "review_model": args.review_model,
        "revision_model": args.revision_model, "base_url": args.base_url,
        "context_tokens": args.context_tokens, "chapters": args.chapters,
        "words_per_chapter": args.words_per_chapter, "profile": args.profile,
        "outline_only": args.outline_only,
        "project": custom_project.model_dump(mode="json") if custom_project else None,
    }
    try:
        prepare_qualification_directory(output, configuration, resume=args.resume)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{(output / 'verification.db').as_posix()}",
        "ARTIFACTS_DIR": str(output / "manuscript"),
        "OLLAMA_BASE_URL": args.base_url,
        "DEFAULT_MODEL": args.model,
        "OLLAMA_NUM_CTX": str(args.context_tokens),
    })
    get_settings.cache_clear()
    settings = Settings().model_copy(update={"worker_exclusive": True})
    with exclusive_worker_lease(settings):
        return run_qualification(args, output, settings, custom_project)


def drive_qualification(session, factory, run_id, settings, provider, *, on_wait=None):
    """Use the production cooldown queue until the isolated run actually settles."""
    last_wait = None
    while True:
        release_provider_retries(session)
        session.commit()
        session.expire_all()
        run = get_run_for_processing(session, run_id)
        if run.status != RunStatus.QUEUED:
            return run
        if run.current_step == "provider_wait":
            if run.error_message != last_wait:
                last_wait = run.error_message
                if on_wait is not None:
                    on_wait(run)
            # Do not hold an open transaction while the provider is offline.
            session.commit()
            time.sleep(min(30.0, max(1.0, settings.worker_poll_interval_seconds)))
            continue
        claimed = claim_next_queued_run(session, worker_id="local-verification")
        session.commit()
        if claimed is None or claimed.id != run_id:
            raise RuntimeError("Isolated qualification could not claim its saved run.")
        with maintain_worker_heartbeat(factory, run_id, "local-verification",
                interval_seconds=min(30.0, settings.run_stale_after_seconds / 3)):
            process_run_safe(session, claimed, settings, provider)
        session.expire_all()


def run_qualification(args, output, settings, custom_project):
    run_migrations()
    factory = build_session_factory(settings)
    with factory() as session:
        configs = ensure_provider_configs(session, settings)
        provider = ProviderManager(settings, configs)
        if not args.resume:
            provider.ensure_model("ollama", args.model)
        routing = {}
        for stage, model in (("autonomous_review", args.review_model), ("autonomous_revision", args.revision_model)):
            if model:
                if not args.resume:
                    provider.ensure_model("ollama", model)
                routing[stage] = {"provider_name": "ollama", "model_name": model}
        install_generation_runtime()
        if args.resume:
            ids = list(session.scalars(select(GenerationRun.id).limit(2)))
            if len(ids) != 1:
                raise ValueError("Qualification resume requires exactly one saved run.")
            recover_running_runs(session, reason="qualification_resume")
            session.commit()
            run = get_run_for_processing(session, ids[0])
            if run.status == RunStatus.FAILED:
                resume_failed_run(session, run)
                session.commit()
            release_provider_retries(session)
            session.commit()
        else:
            project = create_project(session, custom_project or ProjectCreate(
                title="The Last Tide Ledger",
                premise=("Harbor surveyor Mara Vale discovers that the tide ledger was falsified to condemn "
                         "the old ferry district. With retired ferryman Ivo Chen she must prove the fraud "
                         "at a public hearing before the district is demolished. The evidence costs Mara "
                         "her position because her own signature authorized the original survey."),
                desired_word_count=args.chapters * args.words_per_chapter,
                requested_chapters=args.chapters,
                min_words_per_chapter=max(100, args.words_per_chapter * 4 // 5),
                max_words_per_chapter=args.words_per_chapter * 3 // 2,
                preferred_model=args.model,
                story_brief=StoryBrief(
                    genre_profile="mystery",
                    setting="A contemporary coastal harbor; no speculative technology.",
                    protagonist="Mara Vale, harbor surveyor",
                    supporting_cast=["Ivo Chen, retired ferryman"],
                    ending_target="Mara proves the fraud at the hearing, saves the district, and loses her job. Show the aftermath.",
                    world_rules=["The original ledger is paper and cannot be remotely modified.",
                                 "Mara signed the false survey before discovering the fraud."],
                    must_include=["A torn carbon copy planted early becomes evidence at the hearing."],
                    avoid=["Resurrection", "Magic", "A new antagonist or mystery replacing the ending"],
                ),
            ))
            run = create_run(session, project, RunCreate(
                project_id=project.id, pause_after_outline=False, quality_profile=args.profile,
                developmental_rewrite_enabled=args.profile != "draft",
                task_routing=routing,
            ))
            # Only the isolated CLI requests an autonomous outline pause.
            # Ordinary autonomous runs retain their no-approval policy.
            if args.outline_only:
                run.pause_after_outline = True
            session.commit()
        run_id = run.id
        print(json.dumps({"run_id": run_id, "output_dir": str(output), "status": "starting"}), flush=True)
        def report_wait(waiting):
            print(json.dumps({"run_id": run_id, "status": "provider_wait",
                              "message": waiting.error_message}), flush=True)
        run = drive_qualification(session, factory, run_id, settings, provider, on_wait=report_wait)
        report = outline_verification_report(run) if args.outline_only else verification_report(run, settings.artifacts_dir)
        report_path = output / "report.json"
        temporary = output / "report.json.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(report_path)
        print(json.dumps(report, indent=2), flush=True)
        accepted = (report["outline_checks_passed"] if args.outline_only else
                    report["pipeline_complete"] and (args.profile != "autonomous" or report["autonomous_checks_passed"]))
        return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
