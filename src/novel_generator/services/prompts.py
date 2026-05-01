from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ..models import ChapterDraft, GenerationRun, Project
from ..schemas import (
    ChapterContinuityUpdate,
    ChapterCritique,
    ChapterPlan,
    ContinuityLedger,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
)


def _story_brief_lines(project: Project) -> str:
    brief = project.story_brief or {}
    lines = [
        f"Premise: {project.premise}",
        f"Notes: {project.notes or 'None provided.'}",
        f"Setting: {brief.get('setting') or 'Not specified.'}",
        f"Tone: {brief.get('tone') or 'Not specified.'}",
        f"Protagonist: {brief.get('protagonist') or 'Not specified.'}",
        f"Supporting cast: {', '.join(brief.get('supporting_cast', [])) or 'Not specified.'}",
        f"Antagonist: {brief.get('antagonist') or 'Not specified.'}",
        f"Core conflict: {brief.get('core_conflict') or 'Not specified.'}",
        f"Ending target: {brief.get('ending_target') or 'Not specified.'}",
        f"World rules: {', '.join(brief.get('world_rules', [])) or 'Not specified.'}",
        f"Must include: {', '.join(brief.get('must_include', [])) or 'Not specified.'}",
        f"Avoid: {', '.join(brief.get('avoid', [])) or 'Not specified.'}",
    ]
    return "\n".join(lines)


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _normalized_terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-']*", text.lower())


def _filter_canon_registry(
    story_bible: StoryBible | dict[str, Any],
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    plan: ChapterPlan | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    chapter_plan = plan if isinstance(plan, dict) or plan is None else plan.model_dump()

    canon = list(bible.get("canon_registry") or [])
    if not canon:
        return []

    context = " ".join(
        part
        for part in [
            _flatten_text(entry),
            _flatten_text(chapter_plan),
            _flatten_text(ledger.get("open_threads")),
            _flatten_text(ledger.get("active_entities")),
            _flatten_text(ledger.get("open_promises_by_name")),
            _flatten_text(ledger.get("world_state")),
        ]
        if part
    ).lower()
    relevant: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for entity in canon:
        names = [entity.get("name", ""), *(entity.get("aliases") or [])]
        if any(name and name.lower() in context for name in names):
            entity_name = str(entity.get("name", "")).strip().lower()
            if entity_name and entity_name not in seen_names:
                relevant.append(entity)
                seen_names.add(entity_name)

    if relevant:
        return relevant

    active_entities = ledger.get("active_entities") or []
    if active_entities:
        return active_entities[: min(12, len(active_entities))]

    return canon[: min(12, len(canon))]


def outline_summary_from_entry(entry: StructuredOutlineEntry | dict[str, Any]) -> str:
    item = entry if isinstance(entry, dict) else entry.model_dump()
    parts = [
        item.get("objective", "").strip(),
        f"Obstacle: {item.get('primary_obstacle', '').strip()}",
        f"Conflict turn: {item.get('conflict_turn', '').strip()}",
        f"Reveal: {item.get('reveal', '').strip()}",
        f"Cost if success: {item.get('cost_if_success', '').strip()}",
        f"Ending state: {item.get('ending_state', '').strip()}",
    ]
    return " ".join(part for part in parts if part and not part.endswith(":")).strip()


def build_story_bible_messages(project: Project, run: GenerationRun) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a senior developmental editor building a durable story engine for a full novel. "
                "Return valid JSON only with no markdown fences, commentary, or prose outside the JSON object."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Design a story bible for the novel '{project.title}'.\n"
                f"Total target words: {run.target_word_count}. Requested chapters: {run.requested_chapters}.\n"
                f"{_story_brief_lines(project)}\n\n"
                "Return a JSON object with exactly these keys:\n"
                "{\n"
                '  "logline": "string",\n'
                '  "theme": "string",\n'
                '  "act_plan": ["Act I purpose", "Act II purpose", "Act III purpose"],\n'
                '  "cast": [{"name": "string", "role": "string", "desire": "string", "risk": "string"}],\n'
                '  "character_agendas": [\n'
                "    {\n"
                '      "name": "string",\n'
                '      "want": "string",\n'
                '      "fear": "string",\n'
                '      "line_in_sand": "string",\n'
                '      "stance_on_core_conflict": "string",\n'
                '      "relationship_to_protagonist": "string"\n'
                "    }\n"
                "  ],\n"
                '  "canon_registry": [\n'
                "    {\n"
                '      "name": "string",\n'
                '      "kind": "person|faction|system|project|location|artifact",\n'
                '      "role": "string",\n'
                '      "aliases": ["string"]\n'
                "    }\n"
                "  ],\n"
                '  "conflict_ladder": ["escalation beat 1", "escalation beat 2", "escalation beat 3"],\n'
                '  "world_rules": ["rule 1", "rule 2"],\n'
                '  "core_system_rules": ["system rule 1", "system rule 2"],\n'
                '  "prose_guardrails": ["specific warning 1", "specific warning 2"],\n'
                '  "ending_promise": "string"\n'
                "}\n\n"
                "Requirements:\n"
                "- make the protagonist, antagonist, and supporting cast distinct in goal, fear, and moral boundary\n"
                "- include only recurring canonical entities in canon_registry and keep names stable\n"
                "- build a clean escalation ladder toward one primary ending, not multiple competing finales\n"
                "- prose_guardrails must explicitly discourage repeated atmospheric phrasing, thesis-statement endings, and zero-cost technical wins\n"
                "- keep the tone and world rules specific enough to govern later chapters"
            ),
        },
    ]


def build_outline_messages(project: Project, run: GenerationRun, story_bible: StoryBible | dict[str, Any]) -> list[dict[str, str]]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    minimum_setbacks = max(1, math.ceil(run.requested_chapters * 0.3))
    midpoint_start = max(2, math.ceil(run.requested_chapters * 0.4))
    midpoint_end = max(midpoint_start, math.floor(run.requested_chapters * 0.7))
    midpoint_rule = (
        f"- place one major midpoint reversal between chapters {midpoint_start} and {midpoint_end}\n"
        if run.requested_chapters >= 3
        else ""
    )
    return [
        {
            "role": "system",
            "content": (
                "You are a meticulous fiction outliner. Return valid JSON only and make each chapter advance the plot. "
                "Do not repeat the inciting incident, and do not create multiple endings for the book."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Create a {run.requested_chapters}-chapter outline for '{project.title}'.\n"
                f"{_story_brief_lines(project)}\n\n"
                f"Story bible:\n{json.dumps(bible, indent=2)}\n\n"
                "Return a JSON object in this shape:\n"
                "{\n"
                '  "chapters": [\n'
                "    {\n"
                '      "chapter_number": 1,\n'
                '      "act": "Act I",\n'
                '      "title": "string",\n'
                '      "objective": "string",\n'
                '      "conflict_turn": "string",\n'
                '      "character_turn": "string",\n'
                '      "reveal": "string",\n'
                '      "ending_state": "string",\n'
                '      "outcome_type": "win|setback|reversal|compromise",\n'
                '      "primary_obstacle": "string",\n'
                '      "cost_if_success": "string",\n'
                '      "side_character_friction": "string",\n'
                '      "concrete_ending_hook": {\n'
                '        "trigger": "string",\n'
                '        "visible_object_or_actor": "string",\n'
                '        "next_problem": "string"\n'
                "      }\n"
                "    }\n"
                "  ]\n"
                "}\n\n"
                "Rules:\n"
                f"- return exactly {run.requested_chapters} chapters numbered 1 through {run.requested_chapters}\n"
                "- chapter 1 should contain the true inciting incident once and only once as the main discovery beat\n"
                "- no chapter after chapter 1 may rediscover or restate the inciting incident as its primary motion\n"
                "- each later chapter must change the external situation and at least one character state\n"
                f"- at least {minimum_setbacks} chapters must have outcome_type set to setback or reversal\n"
                "- no more than 2 consecutive clean wins are allowed\n"
                f"{midpoint_rule}"
                "- side_character_friction must name who pushes back on the protagonist and why\n"
                "- cost_if_success must describe the price of progress, not just the risk of failure\n"
                "- concrete_ending_hook must end on a specific actor, object, interruption, alarm, arrival, discovery, or reversal\n"
                "- preserve one clean climax and one primary ending in the final chapter"
            ),
        },
    ]


def rolling_context(chapters: list[ChapterDraft], window: int) -> str:
    completed = [chapter for chapter in chapters if chapter.summary]
    recent = completed[-window:]
    if not recent:
        return "No previous chapters yet."
    return "\n".join(
        f"Chapter {chapter.chapter_number}: {chapter.summary}"
        for chapter in recent
    )


def build_chapter_plan_messages(
    project: Project,
    run: GenerationRun,
    chapter: ChapterDraft,
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    prior_context: str,
) -> list[dict[str, str]]:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    relevant_canon = _filter_canon_registry(bible, entry, ledger)
    return [
        {
            "role": "system",
            "content": (
                "You plan fiction scenes. Return valid JSON only. Every chapter plan must visibly advance the story, "
                "must not restate the book premise, and must force the protagonist to pay a real price."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Novel title: {project.title}\n"
                f"Chapter target: {run.min_words_per_chapter}-{run.max_words_per_chapter} words\n"
                f"Recent prose summary:\n{prior_context}\n\n"
                f"Story bible:\n{json.dumps(bible, indent=2)}\n\n"
                f"Relevant canon registry for this chapter:\n{json.dumps(relevant_canon, indent=2)}\n\n"
                f"Continuity ledger:\n{json.dumps(ledger, indent=2)}\n\n"
                f"Current chapter outline:\n{json.dumps(entry, indent=2)}\n\n"
                "Return a JSON object with exactly these keys:\n"
                "{\n"
                '  "opening_state": "string",\n'
                '  "character_goal": "string",\n'
                '  "scene_beats": ["beat 1", "beat 2", "beat 3", "beat 4"],\n'
                '  "conflict_turn": "string",\n'
                '  "ending_hook": "string",\n'
                '  "attempt": "string",\n'
                '  "complication": "string",\n'
                '  "price_paid": "string",\n'
                '  "partial_failure_mode": "string",\n'
                '  "ending_hook_delivery": "string"\n'
                "}\n\n"
                "Rules:\n"
                "- include 4 to 6 concrete scene beats\n"
                "- at least one beat must materially worsen or transform the conflict\n"
                "- if the protagonist uses a technical solution, the plan must include a visible cost or exposure\n"
                "- side characters must exert pressure from their own agendas, not merely help or warn\n"
                "- the ending_hook_delivery must describe the specific final beat that lands the outline's concrete_ending_hook"
            ),
        },
    ]


def build_chapter_draft_messages(
    project: Project,
    run: GenerationRun,
    chapter: ChapterDraft,
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    prior_context: str,
    plan: ChapterPlan | dict[str, Any],
) -> list[dict[str, str]]:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    relevant_canon = _filter_canon_registry(bible, entry, ledger, chapter_plan)
    return [
        {
            "role": "system",
            "content": (
                "You write vivid, coherent fiction chapters. Return prose only with no markdown fences and no chapter heading. "
                "Advance the story, vary sentence openings, and avoid repeated abstract phrasing."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Title: {project.title}\n"
                f"Write chapter {chapter.chapter_number} titled '{chapter.title}'.\n"
                f"Target word range: {run.min_words_per_chapter}-{run.max_words_per_chapter}\n\n"
                f"Story bible:\n{json.dumps(bible, indent=2)}\n\n"
                f"Relevant canon registry:\n{json.dumps(relevant_canon, indent=2)}\n\n"
                f"Continuity ledger:\n{json.dumps(ledger, indent=2)}\n\n"
                f"Recent prose summary:\n{prior_context}\n\n"
                f"Chapter outline:\n{json.dumps(entry, indent=2)}\n\n"
                f"Chapter plan:\n{json.dumps(chapter_plan, indent=2)}\n\n"
                "Hard rules:\n"
                "- do not include a chapter heading or title line\n"
                "- do not repeat the inciting incident unless the situation has materially changed\n"
                "- the chapter must change the external situation and at least one character state\n"
                "- if a technical solution works, show the concrete cost, fallout, or exposure on the page\n"
                "- if side_character_friction exists, the side character must push back from their own agenda\n"
                "- keep names, aliases, systems, projects, and locations consistent with the canon registry\n"
                "- do not introduce abstract chapter endings about destiny, choices, or the future hanging in the balance\n"
                "- end in the exact story state promised by ending_state and land the concrete ending hook with a visible actor, object, or event\n"
                "- keep each named character's voice and priorities distinct\n\n"
                "Return the chapter prose only."
            ),
        },
    ]


def build_chapter_critique_messages(
    project: Project,
    chapter: ChapterDraft,
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    plan: ChapterPlan | dict[str, Any],
    lint_findings: list[str],
) -> list[dict[str, str]]:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    return [
        {
            "role": "system",
            "content": (
                "You are a developmental fiction editor. Return valid JSON only with blunt but useful revision feedback."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Review chapter {chapter.chapter_number} of '{project.title}'.\n\n"
                f"Story bible:\n{json.dumps(bible, indent=2)}\n\n"
                f"Continuity ledger before update:\n{json.dumps(ledger, indent=2)}\n\n"
                f"Chapter outline:\n{json.dumps(entry, indent=2)}\n\n"
                f"Chapter plan:\n{json.dumps(chapter_plan, indent=2)}\n\n"
                f"Deterministic lint findings:\n{json.dumps(lint_findings, indent=2)}\n\n"
                f"Chapter draft:\n{chapter.content or ''}\n\n"
                "Return a JSON object with exactly these keys:\n"
                "{\n"
                '  "strengths": ["string"],\n'
                '  "warnings": ["string"],\n'
                '  "revision_required": true,\n'
                '  "focus": ["string"],\n'
                '  "forward_motion_score": 0,\n'
                '  "ending_concreteness_score": 0,\n'
                '  "cost_consequence_realism_score": 0,\n'
                '  "side_character_independence_score": 0,\n'
                '  "proper_noun_continuity_score": 0,\n'
                '  "repetition_risk_score": 0,\n'
                '  "blocking_issues": ["string"],\n'
                '  "soft_warnings": ["string"],\n'
                '  "repair_scope": "none|targeted_scene_and_ending|full_chapter"\n'
                "}\n\n"
                "Rules:\n"
                "- set revision_required to true if the chapter has an abstract ending, a zero-cost major solution, a repeated premise beat, a side character who only helps or warns, or a proper-noun inconsistency\n"
                "- use repair_scope 'targeted_scene_and_ending' for ending, cost, repetition-fatigue, or side-character pressure problems\n"
                "- use repair_scope 'full_chapter' only when continuity or premise repetition is severe\n"
                "- forward_motion_score, ending_concreteness_score, cost_consequence_realism_score, side_character_independence_score, and proper_noun_continuity_score should be higher when the draft is stronger\n"
                "- repetition_risk_score should be higher when repetition risk is worse"
            ),
        },
    ]


def build_chapter_revision_messages(
    project: Project,
    chapter: ChapterDraft,
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    plan: ChapterPlan | dict[str, Any],
    critique: ChapterCritique | dict[str, Any],
    lint_findings: list[str],
) -> list[dict[str, str]]:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    notes = critique if isinstance(critique, dict) else critique.model_dump()
    return [
        {
            "role": "system",
            "content": (
                "You revise fiction chapters. Return prose only with no chapter heading and incorporate the critique faithfully."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Revise chapter {chapter.chapter_number} of '{project.title}'.\n\n"
                f"Story bible:\n{json.dumps(bible, indent=2)}\n\n"
                f"Continuity ledger:\n{json.dumps(ledger, indent=2)}\n\n"
                f"Chapter outline:\n{json.dumps(entry, indent=2)}\n\n"
                f"Chapter plan:\n{json.dumps(chapter_plan, indent=2)}\n\n"
                f"Critique to fix:\n{json.dumps(notes, indent=2)}\n\n"
                f"Deterministic lint findings to fix:\n{json.dumps(lint_findings, indent=2)}\n\n"
                f"Current draft:\n{chapter.content or ''}\n\n"
                "Revision instructions:\n"
                "- if repair_scope is 'targeted_scene_and_ending', preserve the good material and rewrite only the weakest scene plus the final 2 to 3 paragraphs\n"
                "- if repair_scope is 'full_chapter', rebuild the chapter so it stops repeating the premise and restores continuity\n"
                "- show a real price or fallout if a technical solution succeeds\n"
                "- make side characters push back from their own agendas rather than existing only to help or warn\n"
                "- end on a concrete next problem, not a thesis sentence about the future or a choice\n"
                "- keep names and roles consistent with the canon registry and continuity ledger\n"
                "- do not add a heading\n\n"
                "Return revised chapter prose only."
            ),
        },
    ]


def build_summary_messages(chapter: ChapterDraft, outline_entry: StructuredOutlineEntry | dict[str, Any]) -> list[dict[str, str]]:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    return [
        {
            "role": "system",
            "content": "You summarize fiction chapters for continuity memory. Return plain text only.",
        },
        {
            "role": "user",
            "content": (
                f"Summarize chapter {chapter.chapter_number} in 3 to 5 sentences.\n"
                f"Make sure the summary captures the external change, the character turn, the price paid, and the new ending state.\n\n"
                f"Chapter outline:\n{json.dumps(entry, indent=2)}\n\n"
                f"{chapter.content or ''}"
            ),
        },
    ]


def build_continuity_update_messages(
    project: Project,
    chapter: ChapterDraft,
    current_ledger: ContinuityLedger | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
) -> list[dict[str, str]]:
    ledger = current_ledger if isinstance(current_ledger, dict) else current_ledger.model_dump()
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    relevant_canon = _filter_canon_registry(
        bible,
        {"outline_summary": chapter.outline_summary, "chapter_number": chapter.chapter_number},
        ledger,
    )
    return [
        {
            "role": "system",
            "content": (
                "You maintain a continuity ledger for a novel. Return valid JSON only with the fully updated ledger after this chapter."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Update the continuity ledger for chapter {chapter.chapter_number} of '{project.title}'.\n\n"
                f"Current ledger:\n{json.dumps(ledger, indent=2)}\n\n"
                f"Relevant canon registry:\n{json.dumps(relevant_canon, indent=2)}\n\n"
                f"Chapter summary:\n{chapter.summary or ''}\n\n"
                "Return a JSON object with exactly these keys:\n"
                "{\n"
                '  "chapter_outcome": "string",\n'
                '  "current_patch_status": "string",\n'
                '  "character_states": {"Character": "state"},\n'
                '  "world_state": "string",\n'
                '  "open_threads": ["string"],\n'
                '  "resolved_threads": ["string"],\n'
                '  "timeline_entry": "string",\n'
                '  "timeline": ["string"],\n'
                '  "new_entities_introduced": [\n'
                "    {\n"
                '      "name": "string",\n'
                '      "kind": "person|faction|system|project|location|artifact",\n'
                '      "role": "string",\n'
                '      "aliases": ["string"]\n'
                "    }\n"
                "  ],\n"
                '  "entity_state_changes": {"Entity Name": "what changed"},\n'
                '  "open_promises_by_name": {"promise label": "why it is still live"}\n'
                "}\n\n"
                "Rules:\n"
                "- keep unresolved threads alive unless the chapter truly resolves them\n"
                "- update character states only where the chapter created a real change\n"
                "- append a concise timeline entry for this chapter\n"
                "- only list intentionally new canonical entities in new_entities_introduced\n"
                "- explicitly track which named entities changed state and which open promises are still live"
            ),
        },
    ]


def build_manuscript_qa_messages(
    project: Project,
    story_bible: StoryBible | dict[str, Any],
    lint_findings: list[str],
    chapters: list[ChapterDraft],
) -> list[dict[str, str]]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    chapter_payload = [
        {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "summary": chapter.summary or "",
            "word_count": chapter.word_count,
            "qa_notes": chapter.qa_notes or {},
        }
        for chapter in chapters
    ]
    return [
        {
            "role": "system",
            "content": (
                "You are an editorial QA reviewer for AI-generated fiction. Return valid JSON only with concise, actionable assessment."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Review the manuscript for '{project.title}'.\n\n"
                f"Story bible:\n{json.dumps(bible, indent=2)}\n\n"
                f"Deterministic lint findings:\n{json.dumps(lint_findings, indent=2)}\n\n"
                f"Chapter summaries and QA notes:\n{json.dumps(chapter_payload, indent=2)}\n\n"
                "Return a JSON object with exactly these keys:\n"
                "{\n"
                '  "overall_verdict": "string",\n'
                '  "strengths": ["string"],\n'
                '  "warnings": ["string"],\n'
                '  "continuity_risks": ["string"],\n'
                '  "repetition_risks": ["string"],\n'
                '  "ending_coherence_notes": ["string"],\n'
                '  "lint_findings": ["string"],\n'
                '  "chapter_ending_quality_notes": ["string"],\n'
                '  "easy_win_warnings": ["string"],\n'
                '  "proper_noun_continuity_findings": ["string"],\n'
                '  "side_character_agency_notes": ["string"],\n'
                '  "atmospheric_repetition_findings": ["string"]\n'
                "}\n\n"
                "Be specific about repeated setups, duplicated endings, continuity instability, easy technical wins, side-character flatness, "
                "proper-noun drift, and whether the manuscript delivers on the ending promise."
            ),
        },
    ]


def build_json_repair_messages(raw_text: str, label: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "Repair malformed JSON. Return valid JSON only with no markdown fences or commentary.",
        },
        {
            "role": "user",
            "content": (
                f"The following {label} output should have been valid JSON but is malformed.\n"
                "Repair it into valid JSON while preserving the original meaning as closely as possible.\n\n"
                f"{raw_text}"
            ),
        },
    ]


def extract_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    candidates = [cleaned]
    for opening, closing in (("{", "}"), ("[", "]")):
        start = cleaned.find(opening)
        end = cleaned.rfind(closing)
        if start != -1 and end != -1 and end > start:
            candidates.append(cleaned[start : end + 1])

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        for index, char in enumerate(candidate):
            if char not in "[{":
                continue
            try:
                payload, _ = decoder.raw_decode(candidate[index:])
                return payload
            except json.JSONDecodeError:
                continue
    raise ValueError("Structured model output was not valid JSON.")


def parse_story_bible(text: str) -> StoryBible:
    payload = extract_json_payload(text)
    if isinstance(payload, dict) and "story_bible" in payload:
        payload = payload["story_bible"]
    return StoryBible.model_validate(payload)


def parse_outline(text: str, requested_chapters: int) -> list[dict[str, Any]]:
    payload = extract_json_payload(text)
    if isinstance(payload, dict):
        payload = payload.get("chapters", payload)
    try:
        outline = TypeAdapter(list[StructuredOutlineEntry]).validate_python(payload)
    except ValidationError as exc:
        raise ValueError(f"Outline JSON was invalid: {exc}") from exc

    if len(outline) != requested_chapters:
        raise ValueError(
            f"Outline returned {len(outline)} chapters, but {requested_chapters} were required."
        )

    expected_numbers = list(range(1, requested_chapters + 1))
    actual_numbers = [item.chapter_number for item in outline]
    if actual_numbers != expected_numbers:
        raise ValueError("Outline chapter numbers must run sequentially from 1 to the requested chapter count.")

    minimum_setbacks = max(1, math.ceil(requested_chapters * 0.3))
    setback_count = sum(1 for item in outline if item.outcome_type.lower() in {"setback", "reversal"})
    if setback_count < minimum_setbacks:
        raise ValueError(
            f"Outline must contain at least {minimum_setbacks} setback or reversal chapters, but only {setback_count} were provided."
        )

    clean_win_streak = 0
    for item in outline:
        if item.outcome_type.lower() == "win":
            clean_win_streak += 1
        else:
            clean_win_streak = 0
        if clean_win_streak > 2:
            raise ValueError("Outline contains more than two consecutive clean wins.")

    if requested_chapters >= 3:
        midpoint_start = max(2, math.ceil(requested_chapters * 0.4))
        midpoint_end = max(midpoint_start, math.floor(requested_chapters * 0.7))
        midpoint_has_reversal = any(
            midpoint_start <= item.chapter_number <= midpoint_end and item.outcome_type.lower() == "reversal"
            for item in outline
        )
        if not midpoint_has_reversal:
            raise ValueError(
                f"Outline must include a midpoint reversal between chapters {midpoint_start} and {midpoint_end}."
            )

    return [item.model_dump() for item in outline]


def parse_chapter_plan(text: str) -> ChapterPlan:
    payload = extract_json_payload(text)
    if isinstance(payload, dict) and "plan" in payload:
        payload = payload["plan"]
    return ChapterPlan.model_validate(payload)


def parse_chapter_critique(text: str) -> ChapterCritique:
    payload = extract_json_payload(text)
    if isinstance(payload, dict) and "critique" in payload:
        payload = payload["critique"]
    return ChapterCritique.model_validate(payload)


def parse_continuity_update(text: str) -> ChapterContinuityUpdate:
    payload = extract_json_payload(text)
    if isinstance(payload, dict) and "continuity_update" in payload:
        payload = payload["continuity_update"]
    return ChapterContinuityUpdate.model_validate(payload)


def parse_manuscript_qa_report(text: str) -> ManuscriptQaReport:
    payload = extract_json_payload(text)
    if isinstance(payload, dict) and "qa_report" in payload:
        payload = payload["qa_report"]
    return ManuscriptQaReport.model_validate(payload)


def sanitize_chapter_content(content: str) -> str:
    cleaned = content.strip()
    cleaned = re.sub(
        r"^\s*chapter\s+\d+\s*[:\-\u2014]?\s*[^\n]*\n+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()
