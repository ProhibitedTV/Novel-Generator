"""Current-source editorial evidence, separate from historical draft scorecards."""
from .autonomous_contracts import EditorialReview
from .autonomous_editorial import _hash


def review_coverage(run):
    chapters = sorted(run.chapters, key=lambda chapter: chapter.chapter_number)
    expected = list(range(1, run.requested_chapters + 1))
    source_hashes = {str(chapter.chapter_number): _hash(chapter.content or "") for chapter in chapters}
    manuscript_hash = _hash([(chapter.chapter_number, chapter.content) for chapter in chapters])
    events = sorted(run.events, key=lambda event: event.sequence)
    source_complete = [chapter.chapter_number for chapter in chapters] == expected and all(chapter.content for chapter in chapters)
    accepted = next((event for event in reversed(events)
        if event.event_type == "autonomous_quality_passed" and source_complete
        and (event.payload or {}).get("manuscript_hash") == manuscript_hash), None)
    reviews = {}
    for event in events:
        payload = event.payload or {}
        scope = payload.get("scope")
        if event.event_type != "autonomous_review_completed" or scope not in {"final_chapter", "adjacent_chapters", "whole_book"}:
            continue
        try:
            review = EditorialReview.model_validate(payload.get("review"))
        except ValueError:
            continue
        numbers = review.reviewed_chapters
        if numbers != sorted(set(numbers)) or any(number not in expected or str(number) not in source_hashes for number in numbers):
            continue
        if (scope == "final_chapter" and len(numbers) != 1 or
                scope == "adjacent_chapters" and (len(numbers) != 2 or numbers[1] != numbers[0] + 1) or
                scope == "whole_book" and numbers != expected):
            continue
        bound_hashes = payload.get("source_hashes")
        if bound_hashes is not None:
            if bound_hashes != {str(number): source_hashes.get(str(number)) for number in numbers}:
                continue
        elif not accepted or event.sequence > accepted.sequence:
            # Old events did not store individual hashes. Infer their binding
            # only inside a complete acceptance snapshot matching current prose.
            continue
        reviews[(scope, tuple(numbers))] = {
            "passed": review.passed, "checks": {
                "Plot and causes": review.plot_coherent, "Canon": review.canon_consistent,
                "Prose": review.prose_clean, "Chapter ending" if scope == "final_chapter" else "Ending": review.ending_complete,
            }, "issues": [issue.model_dump() for issue in review.issues],
            "provider": payload.get("provider_name", ""), "model": payload.get("model_name", ""),
            "reviewed_at": event.created_at, "scope": scope,
        }
    chapter_reviews = {numbers[0]: review for (scope, numbers), review in reviews.items() if scope == "final_chapter"}
    pair_reviews = [review for (scope, _), review in reviews.items() if scope == "adjacent_chapters"]
    book = reviews.get(("whole_book", tuple(expected)))
    chapter_passes = sum(review["passed"] for review in chapter_reviews.values())
    pair_passes = sum(review["passed"] for review in pair_reviews)
    complete = bool(accepted and chapter_passes == len(expected) and pair_passes == max(0, len(expected) - 1) and book and book["passed"])
    return {
        "chapter_reviews": chapter_reviews, "chapter_passes": chapter_passes,
        "pair_passes": pair_passes, "pair_target": max(0, len(expected) - 1),
        "whole_book_passed": bool(book and book["passed"]), "whole_book_review": book,
        "accepted": complete, "manuscript_hash": manuscript_hash,
        "draft_scorecards": sum(bool(chapter.qa_notes) for chapter in chapters),
        "report_artifact": next((artifact for artifact in run.artifacts if artifact.kind == "autonomous-quality-report"), None),
    }
