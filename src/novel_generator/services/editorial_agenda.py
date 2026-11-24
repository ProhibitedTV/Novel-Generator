"""Durable author/editor decisions, distinct from current-source QA."""
from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import select

from ..models import RunEvent
from ..repositories import record_event
from ..schemas import DevelopmentalRewritePlan, ManuscriptQaReport

EVENT_TYPE = "editorial_agenda_saved"
VERSION = 1


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def source_manifest(chapters):
    return {str(c.chapter_number): sha256((c.content or "").encode("utf-8")).hexdigest()
            for c in sorted(chapters, key=lambda c: c.chapter_number)}


@dataclass(frozen=True)
class EditorialAgenda:
    qa_report: ManuscriptQaReport
    plan: DevelopmentalRewritePlan
    rewrite_markdown: str
    revised_outline_markdown: str
    developmental_qa_markdown: str
    source_hashes: dict
    agenda_id: str

    @classmethod
    def from_payload(cls, payload):
        if payload.get("version") != VERSION:
            raise ValueError("Unsupported saved editorial agenda version.")
        body = {k: v for k, v in payload.items() if k not in {"agenda_id", "message"}}
        if _digest(body) != payload.get("agenda_id"):
            raise ValueError("Saved editorial agenda checksum does not match.")
        return cls(ManuscriptQaReport.model_validate(body["qa_report"]),
                   DevelopmentalRewritePlan.model_validate(body["plan"]),
                   body["rewrite_markdown"], body["revised_outline_markdown"],
                   body["developmental_qa_markdown"], body["source_hashes"], payload["agenda_id"])


def agenda_event(session, run_id):
    return session.scalar(select(RunEvent).where(
        RunEvent.run_id == run_id, RunEvent.event_type == EVENT_TYPE
    ).order_by(RunEvent.sequence.desc()).limit(1))


def load_agenda(session, run, chapters):
    event = agenda_event(session, run.id)
    if event is None:
        return None
    try:
        agenda = EditorialAgenda.from_payload(event.payload)
        if set(agenda.source_hashes) != set(source_manifest(chapters)):
            raise ValueError("Chapter numbering differs from the original editorial agenda.")
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Cannot resume the saved editorial agenda: {exc} Saved prose is preserved.") from exc
    # This is the original editorial decision for this run, not an assessment
    # of the revised text. Hashes are provenance, not a current QA pass.
    return agenda


def save_agenda(session, run, chapters, qa_report, plan, rewrite, outline, comparison):
    if agenda_event(session, run.id) is not None:
        return load_agenda(session, run, chapters)
    body = {
        "version": VERSION, "source_hashes": source_manifest(chapters),
        "qa_report": qa_report.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
        "rewrite_markdown": rewrite, "revised_outline_markdown": outline,
        "developmental_qa_markdown": comparison,
    }
    payload = {**body, "agenda_id": _digest(body),
               "message": "Original developmental assessment and revision agenda saved."}
    agenda = EditorialAgenda.from_payload(payload)
    record_event(session, run, EVENT_TYPE, payload)
    session.commit()
    return agenda


def agenda_summary(run):
    """Historical revision progress; never used as manuscript acceptance."""
    events = sorted(run.events, key=lambda e: e.sequence)
    saved = next((e for e in reversed(events) if e.event_type == EVENT_TYPE), None)
    if saved is None:
        return None
    try:
        agenda = EditorialAgenda.from_payload(saved.payload)
    except (ValueError, KeyError, TypeError):
        return {"invalid": True}
    # Use the same last-action-wins semantics as the revision engine.
    actions = {n: action for action in agenda.plan.chapter_actions for n in action.chapter_numbers}
    outcomes = {}
    for event in events:
        if event.sequence <= saved.sequence:
            continue
        if event.event_type in {"developmental_chapter_revision_completed", "developmental_chapter_revision_fallback"}:
            outcomes[event.payload.get("chapter_number")] = (
                "Revision saved" if event.event_type.endswith("_completed") else "Original prose retained"
            )
    rows = []
    for number in sorted(int(n) for n in agenda.source_hashes):
        action = actions.get(number)
        kind = action.action if action else "not specified"
        requires_revision = kind not in {"", "keep", "none", "no_change", "not specified"}
        rows.append({"chapter": number, "action": kind.replace("_", " "),
                     "reason": action.reason if action else "",
                     "status": outcomes.get(number, "Pending" if requires_revision else "No revision assigned")})
    return {"invalid": False, "diagnosis": agenda.plan.overall_diagnosis, "rows": rows}
