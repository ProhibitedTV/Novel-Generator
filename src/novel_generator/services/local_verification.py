"""Run a real Ollama manuscript through the worker stack in a fresh, isolated database."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..bootstrap import run_migrations
from ..db import build_session_factory
from ..models import ChapterStatus, RunStatus
from ..repositories import (
    claim_next_queued_run, create_project, create_run, ensure_provider_configs,
    get_run_for_processing,
)
from ..schemas import ProjectCreate, RunCreate, StoryBrief
from ..settings import Settings, get_settings
from .pipeline import process_run_safe
from .providers import ProviderManager
from .runner import install_generation_runtime
from .autonomous_editorial import _hash


def verification_report(run, artifacts_dir: Path) -> dict:
    chapters = sorted(run.chapters, key=lambda chapter: chapter.chapter_number)
    expected = list(range(1, run.requested_chapters + 1))
    complete = (
        run.status == RunStatus.COMPLETED
        and [chapter.chapter_number for chapter in chapters] == expected
        and all(chapter.status == ChapterStatus.COMPLETED and chapter.content
                and chapter.summary and chapter.continuity_update for chapter in chapters)
    )
    artifact_paths = {artifact.kind: str(artifacts_dir / artifact.relative_path) for artifact in run.artifacts}
    exported = {"markdown", "docx", "qa-report"} <= artifact_paths.keys() and all(
        Path(path).is_file() and Path(path).stat().st_size > 0 for path in artifact_paths.values()
    )
    return {
        "run_id": run.id,
        "model": run.model_name,
        "status": run.status.value,
        "quality_profile": getattr(run, "quality_profile", "balanced"),
        "autonomous_checks_passed": bool(complete and exported and any(
            event.event_type == "autonomous_quality_passed"
            and event.payload.get("manuscript_hash") == _hash([(chapter.chapter_number, chapter.content) for chapter in chapters])
            for event in run.events
        )),
        "error": run.error_message,
        "pipeline_complete": bool(complete and exported),
        "target_words": run.target_word_count,
        "actual_words": sum(len((chapter.content or "").split()) for chapter in chapters),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts") / (
        "local-verification-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")))
    parser.add_argument("--chapters", type=int, default=3)
    parser.add_argument("--words-per-chapter", type=int, default=500)
    parser.add_argument("--context-tokens", type=int, default=32768)
    parser.add_argument("--profile", choices=["draft", "balanced", "strict", "autonomous"], default="autonomous")
    args = parser.parse_args()
    if args.chapters < 1 or args.words_per_chapter < 100:
        parser.error("Use at least one chapter and 100 words per chapter.")

    output = args.output_dir.resolve()
    # A fresh directory makes accidentally reusing a production database impossible.
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{(output / 'verification.db').as_posix()}",
        "ARTIFACTS_DIR": str(output / "manuscript"),
        "OLLAMA_BASE_URL": args.base_url,
        "DEFAULT_MODEL": args.model,
        "OLLAMA_NUM_CTX": str(args.context_tokens),
    })
    get_settings.cache_clear()
    settings = Settings()
    run_migrations()
    factory = build_session_factory(settings)
    with factory() as session:
        configs = ensure_provider_configs(session, settings)
        provider = ProviderManager(settings, configs)
        provider.ensure_model("ollama", args.model)
        install_generation_runtime()
        project = create_project(session, ProjectCreate(
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
        ))
        session.commit()
        claim_next_queued_run(session, worker_id="local-verification")
        session.commit()
        run = get_run_for_processing(session, run.id)
        print(json.dumps({"run_id": run.id, "output_dir": str(output), "status": "starting"}), flush=True)
        process_run_safe(session, run, settings, provider)
        session.expire_all()
        run = get_run_for_processing(session, run.id)
        report = verification_report(run, settings.artifacts_dir)
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return 0 if report["pipeline_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
