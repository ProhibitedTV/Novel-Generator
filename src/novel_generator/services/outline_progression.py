"""Plan and verify one book across batch boundaries, with persisted recovery."""
from __future__ import annotations

import json
from pydantic import TypeAdapter

from ..models import GenerationRun, ChapterStatus
from .autonomous_editorial import AutonomousQualityError, _check_context, _events, _hash, _record
from .book_specs import contract_text, run_book_brief
from .outline_contracts import OutlineSpineEntry, OutlineProgressionReview
from .prompts import extract_json_payload
from .structured_schema_runtime import make_schema_marker
from .provider_controls import make_ollama_controls_marker
from .providers import review_model_identity, review_cache_allowed, require_unchanged_review_models


PROGRESSION_RULES = (
    "Judge the entire ordered book, including boundaries between generation batches. Each chapter must add distinct "
    "progress in the requested form, rather than restart an earlier discovery, decision, sacrifice, first event, or ending. "
    "Do not reenact the same irreversible act as if it never happened. A completed treaty, destroyed device, relinquished "
    "estate, or first planting remains completed unless a concrete intervening reversal is planned. Distinguish proposing, "
    "negotiating, ratifying, enacting, and amending an agreement; those can be separate advances. A character can reconsider "
    "a belief, but must not repeatedly discover a truth they already know without a cause. Recurring themes, motifs, "
    "intentional refrains, and similar problems with genuinely changed stakes are allowed. Independent collection pieces "
    "may repeat structures and do not share canon unless the brief says so. Handbooks may revisit concepts with new uses. "
    "Do not force a plot, villain, dramatic crisis, or late climax onto other forms. An early resolution with sustained "
    "aftermath can work; repeating the same 'first' resolution later cannot. Preserve author facts and the requested ending."
)


def compact_entry(entry):
    return {key: entry.get(key, "") for key in
            ("chapter_number", "title", "objective", "conflict_turn", "character_turn", "reveal", "ending_state", "new_progress")}


def locked_prefix(session, run):
    if not run.source_run_id or not run.resume_from_chapter:
        return []
    source = session.get(GenerationRun, run.source_run_id)
    if source is None or not source.outline:
        raise AutonomousQualityError("The continuation has no source outline for its completed prefix.")
    numbers = list(range(1, run.resume_from_chapter))
    written = {ch.chapter_number for ch in run.chapters if ch.status == ChapterStatus.COMPLETED and ch.content
               and ch.summary and ch.continuity_update}
    if not set(numbers) <= written:
        raise AutonomousQualityError("Rebuilding an outline requires complete prose and checkpoints for every preserved chapter.")
    prefix = [row for row in source.outline if row["chapter_number"] in numbers]
    if [row["chapter_number"] for row in prefix] != numbers:
        raise AutonomousQualityError("The source outline is missing a preserved chapter.")
    return prefix


def _context(run, outline, prefix):
    return {"premise": run.project.premise, "author_brief": run_book_brief(run),
            "total_chapters": run.requested_chapters,
            "world_rules": (run.story_bible or {}).get("world_rules", []),
            "ending_promise": (run.story_bible or {}).get("ending_promise", ""),
            "locked_chapters": [row["chapter_number"] for row in prefix],
            "written_prefix": [{"chapter_number": ch.chapter_number, "summary": ch.summary}
                               for ch in run.chapters if ch.chapter_number <= len(prefix)],
            "complete_outline": [compact_entry(row) for row in outline]}


def parse_review(raw, outline):
    review = OutlineProgressionReview.model_validate(extract_json_payload(raw))
    expected = [row["chapter_number"] for row in outline]
    if review.reviewed_chapters != expected:
        raise ValueError(f"Review must cover exactly the complete ordered outline {expected}.")
    for issue in review.issues:
        if issue.chapter_numbers != sorted(set(issue.chapter_numbers)) or not set(issue.chapter_numbers) <= set(expected):
            raise ValueError("Issue references must be unique, ordered source outline chapter numbers.")
    return review


def review_outline(session, run, outline, client, prefix=()):
    from . import pipeline
    if len(outline) < 2:
        return OutlineProgressionReview(reviewed_chapters=[row["chapter_number"] for row in outline], progression_coherent=True, issues=[])
    provider, model = pipeline._resolve_stage_route(client, run, "autonomous_review")
    verifier_stage = "autonomous_adjudication" if (run.task_routing or {}).get("autonomous_adjudication") else "autonomous_revision"
    verify_provider, verify_model = pipeline._resolve_stage_route(client, run, verifier_stage)
    reviewer_identity = review_model_identity(client, provider, model)
    verifier_identity = review_model_identity(client, verify_provider, verify_model)
    config = getattr(client, "settings", None)
    if config is None:
        from ..settings import get_settings
        config = get_settings()
    controls = {key: value for key, value in {
        "think": config.ollama_adjudication_think, "temperature": config.ollama_adjudication_temperature}.items() if value is not None}
    context = _context(run, outline, prefix)
    fingerprint = _hash({"contract": 1, "context": context, "reviewer": [provider, model],
                         "verifier": [verify_provider, verify_model], "controls": controls,
                         "reviewer_identity": reviewer_identity, "verifier_identity": verifier_identity})
    for saved in reversed(_events(run, "outline_progression_review_completed")):
        if saved.get("fingerprint") == fingerprint and review_cache_allowed(reviewer_identity, verifier_identity):
            return parse_review(json.dumps(saved["review"]), outline)
    schema = OutlineProgressionReview.model_json_schema()
    numbers = [row["chapter_number"] for row in outline]
    schema["properties"]["reviewed_chapters"]["const"] = numbers
    schema["$defs"]["OutlineProgressionIssue"]["properties"]["chapter_numbers"]["items"]["enum"] = numbers

    def messages(allegations=None):
        instruction = "You are independently auditing book progression. " + PROGRESSION_RULES + "\n" + contract_text(context["author_brief"])
        if allegations is not None:
            instruction += "\nVerify only the supplied allegations against the entire plan and author contract. Reject unsupported claims. Do not invent replacement defects."
        instruction += "\nEach required defect needs source chapter_numbers and a concrete correction. Optional taste never fails the check. If no required defect remains, progression_coherent is true and issues is empty. Return valid JSON only."
        payload = dict(context)
        if allegations is not None:
            payload["allegations_to_verify"] = [{"chapter_numbers": i.chapter_numbers, "problem": i.problem} for i in allegations]
        built = [{"role": "system", "content": instruction}, make_schema_marker("outline_progression_review", schema=schema),
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        if controls:
            built.insert(0, make_ollama_controls_marker(**controls))
        return built

    def generate(provider_name, model_name, built):
        _check_context(built, client, provider_name)
        return pipeline._generate_structured_output(session, run, client, provider_name, model_name, lambda: built,
            lambda raw: parse_review(raw, outline), "whole-outline progression review", "outline_progression_review")
    review = generate(provider, model, messages())
    if review.issues:
        proposed = review.model_dump()
        review = generate(verify_provider, verify_model, messages(review.issues))
        _record(session, run, "outline_progression_adjudicated", {"message": "Verified outline progression allegations before replanning.",
            "proposed_review": proposed, "review": review.model_dump(), "fingerprint": fingerprint})
    require_unchanged_review_models(client, (provider, model, reviewer_identity), (verify_provider, verify_model, verifier_identity))
    _record(session, run, "outline_progression_review_completed", {"message": "Whole outline progression passed." if review.passed else "Whole outline needs structural repair.",
        "fingerprint": fingerprint, "review": review.model_dump(), "source_outline_hash": _hash(outline),
        "reviewer_identity": reviewer_identity, "verifier_identity": verifier_identity})
    return review


def generate_spine(session, run, bible, client, provider, model, prefix, repairs):
    from . import pipeline
    start = len(prefix) + 1
    numbers = list(range(start, run.requested_chapters + 1))
    identity = {"contract": 1, "bible": bible.model_dump(), "context": _context(run, prefix, prefix),
                "provider": provider, "model": model, "repairs": repairs}
    fingerprint = _hash(identity)
    adapter = TypeAdapter(list[OutlineSpineEntry])
    def parse(raw):
        value = extract_json_payload(raw)
        rows = adapter.validate_python(value.get("chapters") if isinstance(value, dict) else value)
        if [row.chapter_number for row in rows] != numbers:
            raise ValueError(f"The spine must contain exactly chapters {numbers}, in order.")
        return [row.model_dump() for row in rows]
    for saved in reversed(_events(run, "outline_spine_checkpoint")):
        if saved.get("fingerprint") == fingerprint:
            return [*prefix, *parse(json.dumps(saved["spine"]))]
    schema = adapter.json_schema()
    schema["minItems"] = schema["maxItems"] = len(numbers)
    schema["$defs"]["OutlineSpineEntry"]["properties"]["chapter_number"]["enum"] = numbers
    built = [{"role": "system", "content": "Plan one complete book before expanding chapter batches. " + PROGRESSION_RULES
        + "\n" + contract_text(run_book_brief(run))
        + "\nUse a single concise sentence of 10-20 words per objective, ending_state, and new_progress; title at most six words. Explain what each chapter adds that earlier chapters did not. Do not write prose or repeat an already delivered first event. Preserve locked chapters exactly. Return valid JSON only."},
        make_schema_marker("outline_spine", schema=schema),
        {"role": "user", "content": json.dumps({**identity, "start_chapter": start, "end_chapter": run.requested_chapters}, ensure_ascii=False)}]
    _check_context(built, client, provider)
    rows = pipeline._generate_structured_output(session, run, client, provider, model, lambda: built,
        parse, "complete book spine", "outline_spine")
    _record(session, run, "outline_spine_checkpoint", {"message": "Saved the complete book progression spine.",
        "fingerprint": fingerprint, "spine": rows})
    return [*prefix, *rows]


def plan_outline(session, run, bible, client, provider, model, settings):
    from .outline_recovery import generate_outline
    from . import pipeline
    prefix = locked_prefix(session, run)
    repairs = []
    for round_number in range(settings.autonomous_outline_repair_attempts):
        spine = generate_spine(session, run, bible, client, provider, model, prefix, repairs) if run.requested_chapters > 4 else None
        outline = generate_outline(session, run, bible, client, provider, model, prefix=prefix, spine=spine, repairs=repairs)
        outline = pipeline._validated_outline_or_fallback(session, run.project, run, bible, outline, locked_chapters=len(prefix))
        if outline[:len(prefix)] != prefix:
            raise AutonomousQualityError("Outline validation changed a preserved chapter. Saved prose is unchanged; rebuild from an earlier chapter.")
        review = review_outline(session, run, outline, client, prefix)
        if review.passed:
            return outline
        locked = set(row["chapter_number"] for row in prefix)
        if any(set(issue.chapter_numbers) <= locked for issue in review.issues):
            raise AutonomousQualityError("The preserved chapters contain a progression defect. Rebuild from an earlier chapter; saved prose is unchanged.")
        repairs = [{**issue.model_dump(), "source_entries": [compact_entry(row) for row in outline
                   if row["chapter_number"] in issue.chapter_numbers]} for issue in review.issues]
        _record(session, run, "outline_progression_repair_requested", {"message": "Replanning the book to remove confirmed repeated or incompatible outcomes.",
            "round": round_number + 1, "repairs": repairs, "outline_hash": _hash(outline)})
    raise AutonomousQualityError("Whole-outline progression did not pass after automatic replanning. Saved planning checkpoints are preserved; select a stronger outline model before resuming.")
