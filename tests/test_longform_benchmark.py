from __future__ import annotations

from novel_generator.services.longform_benchmark import run_longform_benchmark


def test_longform_benchmark_passes_all_architecture_checks() -> None:
    report = run_longform_benchmark(32)

    assert report["passed"] is True
    assert report["score"] == "8/8"
    assert all(report["checks"].values())
    assert 4 in report["observations"]["recall_chapters"]
    assert report["observations"]["tarin_last_touched_chapter"] == 5
    assert report["observations"]["tarin_dormant_for_chapters"] >= 5
    assert report["observations"]["adaptive_target_words"] > 1800
    assert report["observations"]["ending_phase"] == "resolution_priority"
    assert report["observations"]["memory_output_chars"] <= 6000
    assert report["observations"]["developmental_context_chars"] <= 30000
    assert report["observations"]["qa_context_chars"] <= 30000
