from __future__ import annotations

from types import SimpleNamespace

from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services.ending_debt import compile_ending_debt_audit, ending_debt_note
from novel_generator.services.longform_runtime import (
    _inject_ending_debt_audit,
    _wrap_manuscript_qa_runner,
)


def _chapter(number: int = 32) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        title="The Last Archive",
        summary="Iris exposes the archive conspiracy, but Tarin's role remains publicly disputed.",
        continuity_update={
            "chapter_outcome": "The conspiracy becomes public and the archive cannot return to secrecy.",
            "story_turn": {
                "state_after": "The archive is public; Iris and Tarin must live with the consequences.",
                "permanent_consequence": "The evidence can no longer be erased quietly.",
            },
        },
    )


def _run(*, debt: bool = True) -> SimpleNamespace:
    ledger = {
        "open_threads": [],
        "open_promises_by_name": {},
        "trust_fractures": {},
        "emotional_open_loops": {},
        "memory_damage": {},
        "civilian_pressure_points": [],
    }
    if debt:
        ledger.update(
            {
                "open_threads": ["Iris still does not know whether Tarin betrayed her voluntarily."],
                "open_promises_by_name": {
                    "tarin_trust": "Iris must decide whether trust with Tarin can be rebuilt after exposing the archive conspiracy."
                },
                "trust_fractures": {"Iris/Tarin": "The apparent betrayal remains emotionally unresolved."},
            }
        )
    return SimpleNamespace(
        story_bible={
            "ending_promise": "Iris will expose the archive conspiracy and decide whether trust with Tarin can be rebuilt."
        },
        continuity_ledger=ledger,
        chapters=[_chapter()],
    )


def test_ending_debt_audit_flags_live_state_and_central_promise_overlap() -> None:
    run = _run(debt=True)
    audit = compile_ending_debt_audit(run, run.chapters).payload

    meta = audit["_ending_debt_audit"]
    assert meta["status"] == "requires_editorial_review"
    assert meta["active_live_items"] == 3
    assert meta["central_candidate_count"] >= 1
    assert audit["final_chapter"]["chapter_number"] == 32
    assert any(item["kind"] == "open_promise" for item in audit["central_ending_debt_candidates"])
    assert "intentional aftermath" in meta["note"]


def test_ending_debt_audit_allows_clear_final_checkpoint() -> None:
    run = _run(debt=False)
    audit = compile_ending_debt_audit(run, run.chapters)

    assert audit.payload["_ending_debt_audit"]["status"] == "checkpoint_clear"
    assert audit.payload["_ending_debt_audit"]["active_live_items"] == 0
    assert "no remaining live continuity items" in ending_debt_note(audit)


def test_ending_debt_audit_is_injected_into_manuscript_qa_prompt() -> None:
    run = _run(debt=True)
    messages = [{"role": "user", "content": "Review the manuscript and return JSON."}]

    rewritten = _inject_ending_debt_audit(messages, run=run, chapters=run.chapters)

    assert rewritten[0]["content"].startswith("End-of-book story-debt audit")
    assert '"status":"requires_editorial_review"' in rewritten[0]["content"]
    assert "Review the manuscript and return JSON." in rewritten[0]["content"]


def test_qa_runner_persists_ending_note_and_central_debt_warning() -> None:
    run = _run(debt=True)

    def runner(session: object, run: object, chapters: list[object], client: object):
        report = ManuscriptQaReport(overall_verdict="Looks structurally coherent.")
        return report, "old markdown"

    wrapped = _wrap_manuscript_qa_runner(
        runner,
        render_report=lambda report: "rendered:" + "|".join(report.ending_coherence_notes),
    )
    report, markdown = wrapped(object(), run, run.chapters, object())

    assert any("ending-debt audit found" in note.lower() for note in report.ending_coherence_notes)
    assert any("story-bible ending promise" in warning for warning in report.warnings)
    assert markdown.startswith("rendered:Deterministic ending-debt audit")
