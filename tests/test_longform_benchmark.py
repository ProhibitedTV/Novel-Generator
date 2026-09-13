from __future__ import annotations

from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services.ending_debt import compile_ending_debt_audit
from novel_generator.services.longform_benchmark import build_synthetic_longform_run, run_longform_benchmark
from novel_generator.services.manuscript_qa_context import compile_manuscript_qa_capsules
from novel_generator.services.publication_guard_runtime import compile_publication_blockers


def test_longform_benchmark_passes_all_architecture_checks() -> None:
    report = run_longform_benchmark(32)

    assert report["passed"] is True
    assert report["score"] == "9/9"
    assert all(report["checks"].values())
    assert 4 in report["observations"]["recall_chapters"]
    assert report["observations"]["tarin_last_touched_chapter"] == 5
    assert report["observations"]["tarin_dormant_for_chapters"] >= 5
    assert report["observations"]["adaptive_target_words"] > 1800
    assert report["observations"]["ending_phase"] == "resolution_priority"
    assert report["observations"]["memory_output_chars"] <= 6000
    assert report["observations"]["developmental_context_chars"] <= 30000
    assert report["observations"]["qa_context_chars"] <= 30000
    assert report["observations"]["forward_motion_quality_delta"] <= -1.25
    assert report["observations"]["late_book_length_delta_percent"] <= -25
    assert report["observations"]["quality_risk_flags"]


def test_synthetic_longform_final_qa_retains_prose_and_exposes_publication_blockers() -> None:
    run = build_synthetic_longform_run(32)
    qa_capsules = compile_manuscript_qa_capsules(run.chapters, max_chars=30_000)
    debt = compile_ending_debt_audit(run, run.chapters).payload
    blockers = compile_publication_blockers(run, ManuscriptQaReport())

    assert len(qa_capsules.chapters) == len(run.chapters)
    assert qa_capsules.output_chars <= 30_000
    assert all(row.get("final_prose_closing_excerpt") for row in qa_capsules.chapters)
    assert debt["_ending_debt_audit"]["central_candidate_count"] >= 1
    assert any("ending promise" in blocker for blocker in blockers)
    assert any("materially under target" in blocker for blocker in blockers)
