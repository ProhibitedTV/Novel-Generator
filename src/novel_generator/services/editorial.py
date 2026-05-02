from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any

from ..models import ChapterDraft
from ..schemas import (
    CanonicalEntity,
    ChapterPlan,
    ContinuityLedger,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
)


STOCK_PHRASES = [
    "the weight of",
    "raw emotions",
    "the colony breathed",
    "engineered peace",
    "wild emotions",
    "the future of the colony",
    "mara stared",
]

BREATHER_MODES = {"breather", "aftermath"}

ABSTRACT_ENDING_PATTERNS = [
    r"the next step would decide",
    r"the choice would define",
    r"the future rested",
    r"the truth was waiting",
    r"would shape humanity",
    r"would define the next chapter",
    r"would decide the colony['’]s future",
    r"the colony['’]s future rested",
    r"she had begun her journey",
    r"the colony breathed",
]

TECH_SOLUTION_KEYWORDS = [
    "hack",
    "decrypt",
    "spoof",
    "reroute",
    "override",
    "backdoor",
    "bruteforce",
    "brute force",
    "script",
]

TECHNICAL_FATIGUE_TERMS = [
    "override",
    "backdoor",
    "lockdown",
    "quarantine",
    "countdown",
    "emergency wipe",
    "safe-mode",
    "safe mode",
    "power failure",
    "life-support warning",
    "life support warning",
]

COST_KEYWORDS = [
    "burned",
    "locked out",
    "lost access",
    "exposed",
    "exposure",
    "corrupted",
    "corruption",
    "injured",
    "bleeding",
    "delay",
    "late",
    "alarms",
    "alarm",
    "collateral",
    "damage",
    "fallout",
    "betrayed",
    "rupture",
    "argument",
    "sacrifice",
    "cost",
    "price",
]

CONCRETE_ENDING_CUES = [
    "said",
    "door",
    "alarm",
    "drone",
    "footstep",
    "console",
    "screen",
    "lens",
    "voice",
    "sirens",
    "comm",
    "signal",
    "arrived",
    "opened",
    "blinked",
    "turned",
]

COMMON_PROPER_NOUN_IGNORES = {
    "a",
    "an",
    "and",
    "after",
    "before",
    "but",
    "chapter",
    "he",
    "her",
    "his",
    "i",
    "if",
    "in",
    "it",
    "later",
    "no",
    "she",
    "that",
    "the",
    "their",
    "then",
    "there",
    "they",
    "this",
    "we",
    "when",
    "yes",
}

MEANINGFUL_TERM_IGNORES = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "beside",
    "but",
    "for",
    "from",
    "had",
    "has",
    "have",
    "her",
    "hers",
    "him",
    "his",
    "in",
    "into",
    "its",
    "more",
    "not",
    "of",
    "off",
    "on",
    "one",
    "onto",
    "or",
    "our",
    "out",
    "over",
    "she",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "through",
    "to",
    "under",
    "until",
    "was",
    "were",
    "while",
    "with",
    "would",
    "you",
    "your",
}


@dataclass
class ChapterLintResult:
    blocking_issues: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    repair_scope: str = "none"
    needs_repair: bool = False
    canonical_collision: bool = False

    def combined_findings(self) -> list[str]:
        return [*self.blocking_issues, *self.soft_warnings]


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _opening_signature(content: str, words: int = 24) -> str:
    return " ".join(_normalized_words(content)[:words])


def _normalize_entity_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _meaningful_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9][a-z0-9\-']+", text.lower())
        if len(term) > 2 and term not in MEANINGFUL_TERM_IGNORES
    }


def _entity_payload(entity: CanonicalEntity | dict[str, Any]) -> CanonicalEntity:
    return entity if isinstance(entity, CanonicalEntity) else CanonicalEntity.model_validate(entity)


def _canon_terms(entity: CanonicalEntity | dict[str, Any]) -> list[str]:
    payload = _entity_payload(entity)
    return [payload.name, *payload.aliases]


def _approved_proper_nouns(
    story_bible: StoryBible | dict[str, Any],
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    plan: ChapterPlan | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
) -> set[str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()

    approved: set[str] = set()
    for entity in [*(bible.get("canon_registry") or []), *(ledger.get("active_entities") or [])]:
        for term in _canon_terms(entity):
            if term:
                approved.add(_normalize_entity_key(term))

    approved_sources = [
        entry,
        chapter_plan,
        bible.get("cast") or [],
        bible.get("character_agendas") or [],
    ]
    for source in approved_sources:
        text = str(source)
        for match in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[- ][A-Z0-9][A-Za-z0-9-]*)*\b", text):
            normalized = _normalize_entity_key(match)
            if normalized:
                approved.add(normalized)
    return approved


def _proper_noun_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    multi_token = re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[- ][A-Z0-9][A-Za-z0-9-]*)+\b", text)
    for match in multi_token:
        normalized = _normalize_entity_key(match)
        if normalized:
            candidates.add(normalized)

    singles = Counter(re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b", text))
    for match, count in singles.items():
        lowered = match.lower()
        if count < 2 or lowered in COMMON_PROPER_NOUN_IGNORES:
            continue
        normalized = _normalize_entity_key(match)
        if normalized:
            candidates.add(normalized)
    return candidates


def detect_canonical_entity_collisions(
    existing_entities: list[CanonicalEntity | dict[str, Any]],
    new_entities: list[CanonicalEntity | dict[str, Any]],
) -> list[str]:
    collisions: list[str] = []
    lookup: dict[str, tuple[str, str]] = {}

    def register(entity: CanonicalEntity | dict[str, Any], allow_existing: bool) -> None:
        payload = _entity_payload(entity)
        canonical_key = _normalize_entity_key(payload.name)
        for term in [payload.name, *payload.aliases]:
            key = _normalize_entity_key(term)
            if not key:
                continue
            owner = lookup.get(key)
            if owner and owner[0] != canonical_key:
                collisions.append(
                    f"Canonical entity collision: '{term}' conflicts with existing entity '{owner[1]}'."
                )
            if allow_existing or key not in lookup:
                lookup[key] = (canonical_key, payload.name)

    for entity in existing_entities:
        register(entity, allow_existing=True)
    for entity in new_entities:
        register(entity, allow_existing=False)

    return list(dict.fromkeys(collisions))


def merge_canonical_entities(
    existing_entities: list[CanonicalEntity | dict[str, Any]],
    new_entities: list[CanonicalEntity | dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, CanonicalEntity] = {}
    for entity in [*existing_entities, *new_entities]:
        payload = _entity_payload(entity)
        key = _normalize_entity_key(payload.name)
        current = merged.get(key)
        if current is None:
            merged[key] = payload
            continue
        aliases = sorted({*current.aliases, *payload.aliases})
        merged[key] = current.model_copy(
            update={
                "kind": current.kind or payload.kind,
                "role": current.role or payload.role,
                "aliases": aliases,
            }
        )
    return [entity.model_dump() for entity in merged.values()]


def lint_chapter(
    chapter: ChapterDraft,
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    plan: ChapterPlan | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    prior_chapters: list[ChapterDraft],
) -> ChapterLintResult:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    content = (chapter.content or "").strip()
    lowered = content.lower()
    result = ChapterLintResult()

    tail = lowered[-220:]
    for pattern in ABSTRACT_ENDING_PATTERNS:
        if re.search(pattern, tail):
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} ends in an abstract thesis statement instead of a concrete hook."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
            break

    ending_hook = entry.get("concrete_ending_hook") or {}
    hook_terms = _meaningful_terms(
        " ".join(
            [
                str(ending_hook.get("trigger", "")),
                str(ending_hook.get("visible_object_or_actor", "")),
                str(ending_hook.get("next_problem", "")),
            ]
        )
    )
    tail_terms = _meaningful_terms(content[-320:])
    if hook_terms and not (hook_terms & tail_terms):
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} misses the planned concrete ending hook."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    elif not any(cue in tail for cue in CONCRETE_ENDING_CUES):
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} may still end too abstractly; the final beat lacks a strong visible action cue."
        )

    for phrase in STOCK_PHRASES:
        count = lowered.count(phrase)
        if count >= 2:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} repeats atmospheric phrase '{phrase}' {count} times."
            )

    opening_signature = _opening_signature(content)
    opening_prefix = _opening_signature(content, words=10)
    prior_signatures = {_opening_signature(previous.content or "") for previous in prior_chapters if previous.content}
    prior_prefixes = {_opening_signature(previous.content or "", words=10) for previous in prior_chapters if previous.content}
    if opening_signature and opening_signature in prior_signatures:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} reuses an opening signature from an earlier chapter."
        )
        result.needs_repair = True
        result.repair_scope = result.repair_scope if result.repair_scope != "none" else "targeted_scene_and_ending"
    elif opening_prefix and opening_prefix in prior_prefixes:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} reuses an opening signature from an earlier chapter."
        )
        result.needs_repair = True
        result.repair_scope = result.repair_scope if result.repair_scope != "none" else "targeted_scene_and_ending"

    uses_technical_solution = any(keyword in lowered for keyword in TECH_SOLUTION_KEYWORDS) or any(
        keyword in str(chapter_plan).lower() for keyword in TECH_SOLUTION_KEYWORDS
    )
    has_visible_cost = any(keyword in lowered for keyword in COST_KEYWORDS)
    if uses_technical_solution and not has_visible_cost:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} resolves a technical problem without showing a concrete cost or exposure."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"

    chapter_mode = str(entry.get("chapter_mode", "")).strip().lower()
    if chapter_mode in BREATHER_MODES:
        civilian_terms = _meaningful_terms(str(entry.get("civilian_life_detail", "")))
        emotional_terms = _meaningful_terms(str(entry.get("emotional_reveal", "")))
        ideology_terms = _meaningful_terms(str(entry.get("ideology_pressure", "")))
        content_terms = _meaningful_terms(content)

        if civilian_terms and len(civilian_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned civilian-life detail for a breather or aftermath chapter."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        if emotional_terms and len(emotional_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned emotional reveal for a breather or aftermath chapter."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        if ideology_terms and len(ideology_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned ideology pressure for a breather or aftermath chapter."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        if uses_technical_solution:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is tagged as a breather or aftermath chapter but still centers technical problem-solving."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"

    friction = str(entry.get("side_character_friction", "")).strip()
    if friction:
        friction_terms = _meaningful_terms(friction)
        content_terms = _meaningful_terms(content)
        if friction_terms and len(friction_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned side-character friction."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"

    approved_nouns = _approved_proper_nouns(story_bible, entry, chapter_plan, continuity_ledger)
    candidate_nouns = _proper_noun_candidates(content)
    unknown_nouns = sorted(candidate for candidate in candidate_nouns if candidate not in approved_nouns)
    if unknown_nouns:
        rendered = ", ".join(unknown_nouns[:5])
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} introduces possible unapproved proper nouns: {rendered}."
        )
        result.needs_repair = True
        result.repair_scope = result.repair_scope if result.repair_scope != "none" else "targeted_scene_and_ending"

    fatigue_hits = [term for term in TECHNICAL_FATIGUE_TERMS if term in lowered]
    if len(fatigue_hits) > 2:
        message = (
            f"Chapter {chapter.chapter_number} leans on too many technical emergency beats at once: "
            + ", ".join(fatigue_hits[:4])
            + "."
        )
        if chapter_mode in BREATHER_MODES:
            result.blocking_issues.append(message)
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        else:
            result.soft_warnings.append(message)

    if ledger.get("memory_damage"):
        memory_terms = _meaningful_terms(str(ledger.get("memory_damage")))
        if memory_terms and len(memory_terms & _meaningful_terms(content)) < 1:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be dropping an ongoing memory-damage consequence."
            )
    if ledger.get("trust_fractures"):
        fracture_terms = _meaningful_terms(str(ledger.get("trust_fractures")))
        if fracture_terms and len(fracture_terms & _meaningful_terms(content)) < 1:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be dropping an ongoing trust fracture."
            )
    if ledger.get("civilian_pressure_points"):
        civilian_pressure_terms = _meaningful_terms(str(ledger.get("civilian_pressure_points")))
        if civilian_pressure_terms and len(civilian_pressure_terms & _meaningful_terms(content)) < 1:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be ignoring existing civilian consequences."
            )

    collisions = detect_canonical_entity_collisions(
        ledger.get("active_entities") or [],
        (chapter.continuity_update or {}).get("new_entities_introduced", []),
    )
    if collisions:
        result.blocking_issues.extend(collisions)
        result.canonical_collision = True
        result.needs_repair = True
        result.repair_scope = "full_chapter"

    result.blocking_issues = list(dict.fromkeys(result.blocking_issues))
    result.soft_warnings = list(dict.fromkeys(result.soft_warnings))
    if result.repair_scope == "none" and result.needs_repair:
        result.repair_scope = "targeted_scene_and_ending"
    return result


def lint_manuscript(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    if not chapters:
        return findings

    heading_duplicates = [
        chapter.chapter_number
        for chapter in chapters
        if re.match(r"^\s*chapter\s+\d+\b", (chapter.content or "").strip(), flags=re.IGNORECASE)
    ]
    if heading_duplicates:
        findings.append(
            "Duplicate chapter heading text remained inside chapter prose for chapters "
            + ", ".join(str(number) for number in heading_duplicates)
            + "."
        )

    openings = Counter()
    for chapter in chapters:
        signature = _opening_signature(chapter.content or "")
        if signature:
            openings[signature] += 1
    repeated_openings = [signature for signature, count in openings.items() if count > 1]
    if repeated_openings:
        findings.append("At least two chapters share nearly identical opening paragraphs.")

    manuscript_text = "\n\n".join(chapter.content or "" for chapter in chapters).lower()
    for phrase in STOCK_PHRASES:
        count = manuscript_text.count(phrase)
        if count >= 4:
            findings.append(f"Repeated stock phrase '{phrase}' appears {count} times.")

    for previous, current in zip(chapters, chapters[1:]):
        previous_words = set(_normalized_words(previous.summary or previous.content or ""))
        current_words = set(_normalized_words(current.summary or current.content or ""))
        if previous_words and current_words:
            overlap = len(previous_words & current_words) / max(1, len(current_words))
            if overlap >= 0.85:
                findings.append(
                    f"Chapter {current.chapter_number} appears to restate too much of chapter {previous.chapter_number}."
                )

    final_chapters = chapters[-3:]
    if len(final_chapters) >= 2:
        final_signatures = {_opening_signature(chapter.summary or chapter.content or "", words=16) for chapter in final_chapters}
        if len(final_signatures) < len(final_chapters):
            findings.append("The final act appears to repeat climax or ending beats.")

    for chapter in chapters:
        if not (chapter.summary or "").strip():
            findings.append(f"Chapter {chapter.chapter_number} is missing a continuity summary.")

    return findings


def manuscript_quality_notes(
    chapters: list[ChapterDraft],
    story_bible: StoryBible | dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    bible = None
    if story_bible is not None:
        bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()

    notes = {
        "chapter_ending_quality_notes": [],
        "easy_win_warnings": [],
        "proper_noun_continuity_findings": [],
        "side_character_agency_notes": [],
        "atmospheric_repetition_findings": [],
        "emotional_pacing_notes": [],
        "ideology_consistency_findings": [],
        "civilian_texture_findings": [],
        "technical_escalation_fatigue_findings": [],
    }

    manuscript_text = "\n\n".join(chapter.content or "" for chapter in chapters).lower()
    for chapter in chapters:
        qa = chapter.qa_notes or {}
        warnings = [*qa.get("warnings", []), *qa.get("soft_warnings", []), *qa.get("blocking_issues", [])]
        lowered_warnings = " ".join(warnings).lower()

        if qa.get("ending_concreteness_score", 10) <= 5 or "ending" in lowered_warnings:
            notes["chapter_ending_quality_notes"].append(
                f"Chapter {chapter.chapter_number} still shows ending risk: {', '.join(warnings[:2]) or 'ending needs sharper specificity.'}"
            )
        if qa.get("cost_consequence_realism_score", 10) <= 5 or "cost" in lowered_warnings or "technical problem" in lowered_warnings:
            notes["easy_win_warnings"].append(
                f"Chapter {chapter.chapter_number} may still resolve a major problem too cleanly."
            )
        if qa.get("side_character_independence_score", 10) <= 5 or "side-character" in lowered_warnings:
            notes["side_character_agency_notes"].append(
                f"Chapter {chapter.chapter_number} may need stronger side-character resistance or competing goals."
            )
        if qa.get("emotional_depth_score", 10) <= 5 or "emotional" in lowered_warnings or "memory-damage consequence" in lowered_warnings:
            notes["emotional_pacing_notes"].append(
                f"Chapter {chapter.chapter_number} may need more emotional aftermath or breathing room."
            )
        if qa.get("ideology_clarity_score", 10) <= 5 or "ideology" in lowered_warnings:
            notes["ideology_consistency_findings"].append(
                f"Chapter {chapter.chapter_number} may be blurring or contradicting a character's beliefs."
            )
        if qa.get("civilian_texture_score", 10) <= 5 or "civilian" in lowered_warnings:
            notes["civilian_texture_findings"].append(
                f"Chapter {chapter.chapter_number} may need more concrete civilian-life texture."
            )
        if "proper noun" in lowered_warnings or "canonical entity collision" in lowered_warnings or qa.get("proper_noun_continuity_score", 10) <= 5:
            notes["proper_noun_continuity_findings"].append(
                f"Chapter {chapter.chapter_number} raised proper-noun continuity concerns."
            )
        if "technical emergency beats" in lowered_warnings:
            notes["technical_escalation_fatigue_findings"].append(
                f"Chapter {chapter.chapter_number} may be too dense with emergency-system language."
            )
        for phrase in STOCK_PHRASES:
            count = (chapter.content or "").lower().count(phrase)
            if count >= 2:
                notes["atmospheric_repetition_findings"].append(
                    f"Chapter {chapter.chapter_number} repeats '{phrase}' {count} times."
                )

    if bible:
        for entity in bible.get("canon_registry") or []:
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            kind = str(entity.get("kind", "")).strip().lower()
            aliases = [alias for alias in entity.get("aliases", []) if alias]
            canonical_mentions = manuscript_text.count(name.lower())
            alias_mentions = sum(manuscript_text.count(alias.lower()) for alias in aliases)
            if canonical_mentions == 0 and kind in {"project", "faction", "system", "location"}:
                notes["proper_noun_continuity_findings"].append(
                    f"Canonical {kind} '{name}' never appears in the manuscript."
                )
            if alias_mentions > 0 and canonical_mentions == 0:
                notes["proper_noun_continuity_findings"].append(
                    f"Alias drift risk: aliases for '{name}' appear without the canonical name."
                )

    notes["chapter_ending_quality_notes"] = list(dict.fromkeys(notes["chapter_ending_quality_notes"]))
    notes["easy_win_warnings"] = list(dict.fromkeys(notes["easy_win_warnings"]))
    notes["proper_noun_continuity_findings"] = list(dict.fromkeys(notes["proper_noun_continuity_findings"]))
    notes["side_character_agency_notes"] = list(dict.fromkeys(notes["side_character_agency_notes"]))
    notes["atmospheric_repetition_findings"] = list(dict.fromkeys(notes["atmospheric_repetition_findings"]))
    notes["emotional_pacing_notes"] = list(dict.fromkeys(notes["emotional_pacing_notes"]))
    notes["ideology_consistency_findings"] = list(dict.fromkeys(notes["ideology_consistency_findings"]))
    notes["civilian_texture_findings"] = list(dict.fromkeys(notes["civilian_texture_findings"]))
    notes["technical_escalation_fatigue_findings"] = list(dict.fromkeys(notes["technical_escalation_fatigue_findings"]))
    return notes


def render_qa_report_markdown(report: ManuscriptQaReport) -> str:
    sections = [
        "# Manuscript QA Report",
        "",
        f"**Overall verdict:** {report.overall_verdict}",
        "",
        "## Strengths",
        "",
        *([f"- {item}" for item in report.strengths] or ["- No strengths recorded."]),
        "",
        "## Warnings",
        "",
        *([f"- {item}" for item in report.warnings] or ["- No major warnings recorded."]),
        "",
        "## Continuity Risks",
        "",
        *([f"- {item}" for item in report.continuity_risks] or ["- No continuity risks recorded."]),
        "",
        "## Repetition Risks",
        "",
        *([f"- {item}" for item in report.repetition_risks] or ["- No repetition risks recorded."]),
        "",
        "## Ending Coherence Notes",
        "",
        *([f"- {item}" for item in report.ending_coherence_notes] or ["- No ending notes recorded."]),
        "",
        "## Chapter Ending Quality",
        "",
        *([f"- {item}" for item in report.chapter_ending_quality_notes] or ["- No chapter ending quality notes recorded."]),
        "",
        "## Easy Win Warnings",
        "",
        *([f"- {item}" for item in report.easy_win_warnings] or ["- No easy-win warnings recorded."]),
        "",
        "## Proper Noun Continuity",
        "",
        *([f"- {item}" for item in report.proper_noun_continuity_findings] or ["- No proper-noun continuity findings recorded."]),
        "",
        "## Side Character Agency",
        "",
        *([f"- {item}" for item in report.side_character_agency_notes] or ["- No side-character agency notes recorded."]),
        "",
        "## Atmospheric Repetition",
        "",
        *([f"- {item}" for item in report.atmospheric_repetition_findings] or ["- No atmospheric repetition findings recorded."]),
        "",
        "## Emotional Pacing",
        "",
        *([f"- {item}" for item in report.emotional_pacing_notes] or ["- No emotional pacing notes recorded."]),
        "",
        "## Ideology Consistency",
        "",
        *([f"- {item}" for item in report.ideology_consistency_findings] or ["- No ideology consistency findings recorded."]),
        "",
        "## Civilian Texture",
        "",
        *([f"- {item}" for item in report.civilian_texture_findings] or ["- No civilian texture findings recorded."]),
        "",
        "## Technical Escalation Fatigue",
        "",
        *([f"- {item}" for item in report.technical_escalation_fatigue_findings] or ["- No technical escalation fatigue findings recorded."]),
        "",
        "## Deterministic Lint Findings",
        "",
        *([f"- {item}" for item in report.lint_findings] or ["- No deterministic lint findings recorded."]),
        "",
    ]
    return "\n".join(sections).strip() + "\n"
