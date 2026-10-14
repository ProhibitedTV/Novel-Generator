from datetime import datetime
from types import SimpleNamespace

from novel_generator.services.autonomous_editorial import _hash
from novel_generator.services.editorial_coverage import review_coverage


def book(count=24):
    return SimpleNamespace(requested_chapters=count, artifacts=[], events=[], chapters=[
        SimpleNamespace(chapter_number=n, content=f"Final prose for chapter {n}.", qa_notes={} if n != 7 else {"score": 8})
        for n in range(1, count + 1)
    ])


def audit(run, scope, numbers, *, bound=False, review=None):
    payload = {"scope": scope, "review": review or {
        "reviewed_chapters": numbers, "plot_coherent": True, "canon_consistent": True,
        "prose_clean": True, "ending_complete": True, "issues": [],
    }}
    if bound:
        payload["source_hashes"] = {str(n): _hash(run.chapters[n - 1].content) for n in numbers}
    run.events.append(SimpleNamespace(event_type="autonomous_review_completed", payload=payload,
        sequence=len(run.events) + 1, created_at=datetime(2026, 10, 13)))


def accept(run):
    run.events.append(SimpleNamespace(event_type="autonomous_quality_passed", sequence=len(run.events) + 1,
        payload={"manuscript_hash": _hash([(c.chapter_number, c.content) for c in run.chapters])}))


def review_all(run, *, bound=False):
    for n in range(1, run.requested_chapters + 1):
        audit(run, "final_chapter", [n], bound=bound)
    for n in range(1, run.requested_chapters):
        audit(run, "adjacent_chapters", [n, n + 1], bound=bound)
    audit(run, "whole_book", list(range(1, run.requested_chapters + 1)), bound=bound)


def test_legacy_completed_book_counts_final_audits_not_one_retained_scorecard():
    run = book()
    review_all(run)
    accept(run)
    coverage = review_coverage(run)
    assert (coverage["chapter_passes"], coverage["pair_passes"], coverage["draft_scorecards"]) == (24, 23, 1)
    assert coverage["whole_book_passed"] and coverage["accepted"]
    run.chapters[11].content += " Changed source."
    coverage = review_coverage(run)
    assert coverage["chapter_passes"] == coverage["pair_passes"] == 0
    assert not coverage["accepted"] and not coverage["whole_book_passed"]


def test_source_bound_audits_invalidate_only_changed_chapter_and_pairs():
    run = book(4)
    review_all(run, bound=True)
    accept(run)
    run.chapters[1].content += " A new ending."
    coverage = review_coverage(run)
    assert set(coverage["chapter_reviews"]) == {1, 3, 4}
    assert coverage["pair_passes"] == 1
    assert not coverage["whole_book_passed"] and not coverage["accepted"]
    audit(run, "final_chapter", [2], bound=True)
    assert review_coverage(run)["chapter_passes"] == 4
    assert not review_coverage(run)["accepted"]


def test_acceptance_marker_alone_does_not_fabricate_missing_audits():
    run = book(2)
    accept(run)
    coverage = review_coverage(run)
    assert coverage["chapter_passes"] == 0 and not coverage["accepted"]
    audit(run, "final_chapter", [1])  # Unbound legacy event after acceptance is not certified.
    assert review_coverage(run)["chapter_passes"] == 0


def test_latest_failed_audit_replaces_pass_and_malformed_audits_are_not_passes():
    run = book(2)
    review_all(run, bound=True)
    accept(run)
    failed = {"reviewed_chapters": [1], "plot_coherent": False, "canon_consistent": True,
        "prose_clean": True, "ending_complete": True, "issues": [{"chapter_number": 1,
        "category": "causality", "problem": "Missing cause", "evidence": "Final prose",
        "repair_instruction": "Add the cause"}]}
    audit(run, "final_chapter", [1], bound=True, review=failed)
    audit(run, "final_chapter", [2], bound=True, review={"reviewed_chapters": [2], "issues": []})
    coverage = review_coverage(run)
    assert coverage["chapter_passes"] == 1
    assert not coverage["chapter_reviews"][1]["passed"] and not coverage["accepted"]


def test_no_missing_or_duplicate_sources_count_as_complete():
    run = book(2)
    review_all(run)
    accept(run)
    run.chapters.pop()
    assert not review_coverage(run)["accepted"]
    assert review_coverage(run)["chapter_passes"] == 0
