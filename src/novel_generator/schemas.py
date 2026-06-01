from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import ChapterStatus, RunStatus
from .services.genre_profiles import DEFAULT_GENRE_PROFILE, GENRE_PROFILES


QUALITY_PROFILE_VALUES = {"draft", "balanced", "strict"}

ALLOWED_CHAPTER_MODES = {
    "investigation",
    "interpersonal_confrontation",
    "public_debate",
    "family_private_emotional_scene",
    "physical_escape",
    "civic_fallout",
    "technical_operation",
    "moral_negotiation",
    "quiet_reflection",
    "public_uprising",
    "governance_rebuilding",
    "systems_crisis",
    "breather",
    "aftermath",
    "reversal",
}

CHAPTER_MODE_ALIASES = {
    "confrontation": "interpersonal_confrontation",
    "interpersonal confrontation": "interpersonal_confrontation",
    "public debate": "public_debate",
    "family/private emotional scene": "family_private_emotional_scene",
    "family private emotional scene": "family_private_emotional_scene",
    "physical escape": "physical_escape",
    "civic fallout / civilian aftermath": "civic_fallout",
    "civic fallout": "civic_fallout",
    "civilian aftermath": "civic_fallout",
    "technical operation": "technical_operation",
    "moral negotiation": "moral_negotiation",
    "quiet reflection": "quiet_reflection",
    "public uprising": "public_uprising",
    "governance/rebuilding": "governance_rebuilding",
    "governance rebuilding": "governance_rebuilding",
    "systems crisis": "systems_crisis",
}


def normalize_chapter_mode(value: Any) -> str:
    rendered = str(value or "").strip().lower().replace("-", "_")
    rendered = " ".join(rendered.split())
    rendered = CHAPTER_MODE_ALIASES.get(rendered, rendered.replace(" ", "_"))
    return rendered


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.splitlines()]
        return [item for item in items if item]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _clean_list_map(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for key, items in value.items():
        name = str(key).strip()
        if not name:
            continue
        cleaned_items = _clean_list(items)
        if cleaned_items:
            cleaned[name] = cleaned_items
    return cleaned


def _validate_genre_profile(value: Any) -> str:
    key = str(value or DEFAULT_GENRE_PROFILE).strip() or DEFAULT_GENRE_PROFILE
    if key not in GENRE_PROFILES:
        raise ValueError("Choose one of the supported genre profiles.")
    return key


def _validate_quality_profile(value: Any) -> str:
    key = str(value or "balanced").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in QUALITY_PROFILE_VALUES:
        raise ValueError("Choose draft, balanced, or strict.")
    return key


class StoryCastMember(BaseModel):
    name: str
    role: str
    desire: str
    risk: str


class CharacterAgenda(BaseModel):
    name: str
    want: str
    fear: str
    line_in_sand: str
    stance_on_core_conflict: str
    relationship_to_protagonist: str
    public_belief: str = ""
    private_pressure: str = ""
    stress_response: str = ""


class CanonicalEntity(BaseModel):
    name: str
    kind: str
    role: str = ""
    aliases: list[str] = Field(default_factory=list)
    approved: bool = False
    locked: bool = False

    @field_validator("aliases", mode="before")
    @classmethod
    def validate_aliases(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("name", "kind", "role", mode="before")
    @classmethod
    def validate_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class StoryBrief(BaseModel):
    genre_profile: str = DEFAULT_GENRE_PROFILE
    setting: str = ""
    tone: str = ""
    protagonist: str = ""
    supporting_cast: list[str] = Field(default_factory=list)
    antagonist: str = ""
    core_conflict: str = ""
    ending_target: str = ""
    world_rules: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    style_reference: str = ""
    style_targets: list[str] = Field(default_factory=list)
    dialogue_targets: list[str] = Field(default_factory=list)
    style_avoid: list[str] = Field(default_factory=list)
    approved_canon: list[CanonicalEntity] = Field(default_factory=list)

    @field_validator(
        "supporting_cast",
        "world_rules",
        "must_include",
        "avoid",
        "style_targets",
        "dialogue_targets",
        "style_avoid",
        mode="before",
    )
    @classmethod
    def validate_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator(
        "setting",
        "tone",
        "protagonist",
        "antagonist",
        "core_conflict",
        "ending_target",
        "style_reference",
        mode="before",
    )
    @classmethod
    def validate_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("genre_profile", mode="before")
    @classmethod
    def validate_genre_profile(cls, value: Any) -> str:
        return _validate_genre_profile(value)


class ConcreteEndingHook(BaseModel):
    trigger: str = ""
    visible_object_or_actor: str = ""
    next_problem: str = ""


class ProseStyleProfile(BaseModel):
    narrative_voice: str = ""
    sentence_rhythm: str = ""
    imagery_palette: list[str] = Field(default_factory=list)
    dialogue_rules: list[str] = Field(default_factory=list)
    character_voice_map: dict[str, str] = Field(default_factory=dict)
    avoid: list[str] = Field(default_factory=list)

    @field_validator("imagery_palette", "dialogue_rules", "avoid", mode="before")
    @classmethod
    def validate_style_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("narrative_voice", "sentence_rhythm", mode="before")
    @classmethod
    def validate_style_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("character_voice_map", mode="before")
    @classmethod
    def validate_character_voice_map(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }


class StoryBible(BaseModel):
    genre_profile: str = DEFAULT_GENRE_PROFILE
    logline: str
    theme: str
    act_plan: list[str] = Field(default_factory=list)
    cast: list[StoryCastMember] = Field(default_factory=list)
    character_agendas: list[CharacterAgenda] = Field(default_factory=list)
    canon_registry: list[CanonicalEntity] = Field(default_factory=list)
    conflict_ladder: list[str] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    core_system_rules: list[str] = Field(default_factory=list)
    prose_guardrails: list[str] = Field(default_factory=list)
    genre_contract: list[str] = Field(default_factory=list)
    style_profile: ProseStyleProfile = Field(default_factory=ProseStyleProfile)
    ending_promise: str

    @field_validator(
        "act_plan",
        "conflict_ladder",
        "world_rules",
        "core_system_rules",
        "prose_guardrails",
        "genre_contract",
        mode="before",
    )
    @classmethod
    def validate_story_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("genre_profile", mode="before")
    @classmethod
    def validate_story_genre_profile(cls, value: Any) -> str:
        return _validate_genre_profile(value)


class StructuredOutlineEntry(BaseModel):
    chapter_number: int = Field(ge=1)
    act: str
    title: str
    objective: str
    conflict_turn: str
    character_turn: str
    reveal: str
    ending_state: str
    outcome_type: str = ""
    primary_obstacle: str = ""
    cost_if_success: str = ""
    side_character_friction: str = ""
    independent_side_character_move: str = ""
    concrete_ending_hook: ConcreteEndingHook = Field(default_factory=ConcreteEndingHook)
    chapter_mode: str = ""
    civilian_life_detail: str = ""
    emotional_reveal: str = ""
    ideology_pressure: str = ""
    genre_specific_beats: list[str] = Field(default_factory=list)
    genre_state_change: str = ""

    @field_validator("genre_specific_beats", mode="before")
    @classmethod
    def validate_genre_specific_beats(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("chapter_mode", mode="before")
    @classmethod
    def validate_chapter_mode(cls, value: Any) -> str:
        return normalize_chapter_mode(value)


class SystemStateTransition(BaseModel):
    system_name: str = ""
    previous_state: str = ""
    new_state: str = ""
    cause: str = ""
    chapter_number: int = 0

    @field_validator("system_name", "previous_state", "new_state", "cause", mode="before")
    @classmethod
    def validate_transition_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("chapter_number", mode="before")
    @classmethod
    def validate_chapter_number(cls, value: Any) -> int:
        if value in (None, ""):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0


class ContinuityBibleRow(BaseModel):
    item_type: str = ""
    name: str = ""
    canon_status: str = ""
    observed_status: str = ""
    notes: str = ""

    @field_validator("item_type", "name", "canon_status", "observed_status", "notes", mode="before")
    @classmethod
    def validate_row_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class ContinuityLedger(BaseModel):
    current_patch_status: str
    character_states: dict[str, str] = Field(default_factory=dict)
    world_state: str
    open_threads: list[str] = Field(default_factory=list)
    resolved_threads: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    active_entities: list[CanonicalEntity] = Field(default_factory=list)
    entity_state_changes: dict[str, str] = Field(default_factory=dict)
    open_promises_by_name: dict[str, str] = Field(default_factory=dict)
    ideology_state_by_character: dict[str, str] = Field(default_factory=dict)
    memory_damage: dict[str, str] = Field(default_factory=dict)
    trust_fractures: dict[str, str] = Field(default_factory=dict)
    civilian_pressure_points: list[str] = Field(default_factory=list)
    emotional_open_loops: dict[str, str] = Field(default_factory=dict)
    side_character_decisions: dict[str, list[str]] = Field(default_factory=dict)
    genre_state: dict[str, str] = Field(default_factory=dict)
    system_state_by_name: dict[str, str] = Field(default_factory=dict)
    system_state_transitions: list[SystemStateTransition] = Field(default_factory=list)

    @field_validator("side_character_decisions", mode="before")
    @classmethod
    def validate_side_character_decisions(cls, value: Any) -> dict[str, list[str]]:
        return _clean_list_map(value)

    @field_validator("system_state_transitions", mode="before")
    @classmethod
    def validate_system_state_transitions(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        return []


class ChapterStoryTurn(BaseModel):
    irreversible_change: str = ""
    protagonist_choice: str = ""
    choice_alternatives: list[str] = Field(default_factory=list)
    permanent_consequence: str = ""
    why_this_chapter_cannot_be_cut: str = ""
    state_before: str = ""
    state_after: str = ""

    @field_validator("choice_alternatives", mode="before")
    @classmethod
    def validate_choice_alternatives(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator(
        "irreversible_change",
        "protagonist_choice",
        "permanent_consequence",
        "why_this_chapter_cannot_be_cut",
        "state_before",
        "state_after",
        mode="before",
    )
    @classmethod
    def validate_story_turn_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class ChapterPlan(BaseModel):
    chapter_mode: str = ""
    opening_state: str
    character_goal: str
    scene_beats: list[str] = Field(default_factory=list)
    conflict_turn: str
    ending_hook: str
    attempt: str = ""
    complication: str = ""
    price_paid: str = ""
    partial_failure_mode: str = ""
    ending_hook_delivery: str = ""
    emotional_anchor: str = ""
    civilian_texture: str = ""
    ideology_clash: str = ""
    primary_interpersonal_conflict: str = ""
    independent_side_character_move: str = ""
    genre_specific_focus: str = ""
    genre_specific_beats: list[str] = Field(default_factory=list)
    story_turn: "ChapterStoryTurn" = Field(default_factory=lambda: ChapterStoryTurn())

    @field_validator("scene_beats", "genre_specific_beats", mode="before")
    @classmethod
    def validate_plan_genre_specific_beats(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("chapter_mode", mode="before")
    @classmethod
    def validate_chapter_mode(cls, value: Any) -> str:
        return normalize_chapter_mode(value)


class ChapterContinuityUpdate(BaseModel):
    chapter_outcome: str
    current_patch_status: str
    character_states: dict[str, str] = Field(default_factory=dict)
    world_state: str
    open_threads: list[str] = Field(default_factory=list)
    resolved_threads: list[str] = Field(default_factory=list)
    timeline_entry: str
    timeline: list[str] = Field(default_factory=list)
    new_entities_introduced: list[CanonicalEntity] = Field(default_factory=list)
    entity_state_changes: dict[str, str] = Field(default_factory=dict)
    open_promises_by_name: dict[str, str] = Field(default_factory=dict)
    ideology_state_by_character: dict[str, str] = Field(default_factory=dict)
    ideology_shift_notes: dict[str, str] = Field(default_factory=dict)
    memory_damage: dict[str, str] = Field(default_factory=dict)
    trust_fractures: dict[str, str] = Field(default_factory=dict)
    civilian_pressure_points: list[str] = Field(default_factory=list)
    emotional_open_loops: dict[str, str] = Field(default_factory=dict)
    side_character_decisions: dict[str, list[str]] = Field(default_factory=dict)
    story_turn: "ChapterStoryTurn" = Field(default_factory=lambda: ChapterStoryTurn())
    genre_state: dict[str, str] = Field(default_factory=dict)
    system_state_transitions: list[SystemStateTransition] = Field(default_factory=list)

    @field_validator(
        "open_threads",
        "resolved_threads",
        "timeline",
        "civilian_pressure_points",
        mode="before",
    )
    @classmethod
    def validate_continuity_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("side_character_decisions", mode="before")
    @classmethod
    def validate_side_character_decisions(cls, value: Any) -> dict[str, list[str]]:
        return _clean_list_map(value)

    @field_validator("system_state_transitions", mode="before")
    @classmethod
    def validate_system_state_transitions(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        return []


class ChapterCritique(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    revision_required: bool = False
    focus: list[str] = Field(default_factory=list)
    ending_hook_type: str = "unknown"
    forward_motion_score: int = Field(default=0, ge=0, le=10)
    ending_concreteness_score: int = Field(default=0, ge=0, le=10)
    scene_turn_resolution_score: int = Field(default=10, ge=0, le=10)
    cost_consequence_realism_score: int = Field(default=0, ge=0, le=10)
    side_character_independence_score: int = Field(default=0, ge=0, le=10)
    proper_noun_continuity_score: int = Field(default=0, ge=0, le=10)
    repetition_risk_score: int = Field(default=0, ge=0, le=10)
    emotional_depth_score: int = Field(default=0, ge=0, le=10)
    ideology_clarity_score: int = Field(default=0, ge=0, le=10)
    civilian_texture_score: int = Field(default=0, ge=0, le=10)
    genre_contract_score: int = Field(default=10, ge=0, le=10)
    style_alignment_score: int = Field(default=10, ge=0, le=10)
    voice_distinctness_score: int = Field(default=10, ge=0, le=10)
    sentence_rhythm_score: int = Field(default=10, ge=0, le=10)
    sensory_specificity_score: int = Field(default=10, ge=0, le=10)
    dialogue_tension_score: int = Field(default=10, ge=0, le=10)
    technical_escalation_fatigue_score: int = Field(default=0, ge=0, le=10)
    irreversibility_score: int = Field(default=10, ge=0, le=10)
    choice_clarity_score: int = Field(default=10, ge=0, le=10)
    cuttable_chapter_risk_score: int = Field(default=0, ge=0, le=10)
    blocking_issues: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    genre_contract_findings: list[str] = Field(default_factory=list)
    repair_scope: str = "none"

    @field_validator(
        "strengths",
        "warnings",
        "focus",
        "blocking_issues",
        "soft_warnings",
        "genre_contract_findings",
        mode="before",
    )
    @classmethod
    def validate_note_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator(
        "forward_motion_score",
        "ending_concreteness_score",
        "scene_turn_resolution_score",
        "cost_consequence_realism_score",
        "side_character_independence_score",
        "proper_noun_continuity_score",
        "repetition_risk_score",
        "emotional_depth_score",
        "ideology_clarity_score",
        "civilian_texture_score",
        "genre_contract_score",
        "style_alignment_score",
        "voice_distinctness_score",
        "sentence_rhythm_score",
        "sensory_specificity_score",
        "dialogue_tension_score",
        "technical_escalation_fatigue_score",
        "irreversibility_score",
        "choice_clarity_score",
        "cuttable_chapter_risk_score",
        mode="before",
    )
    @classmethod
    def normalize_scores(cls, value: Any) -> int:
        if value is None or value == "":
            return 0

        if isinstance(value, str):
            value = value.strip()
            if not value:
                return 0

        numeric = float(value)
        if numeric > 10:
            numeric = numeric / 10 if numeric <= 100 else 10

        normalized = int(math.floor(numeric + 0.5))
        return max(0, min(10, normalized))

    @field_validator("ending_hook_type", mode="before")
    @classmethod
    def normalize_ending_hook_type(cls, value: Any) -> str:
        normalized = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"action_hook", "concrete_hook"}:
            return "concrete_action_hook"
        if normalized in {"resolved_turn", "scene_turn", "resolved_scene"}:
            return "resolved_scene_turn"
        if normalized in {"image_beat", "feeling_beat", "image_feeling_beat"}:
            return "image_or_feeling_beat"
        if normalized in {"summary", "planning_language", "outline_contract"}:
            return "outline_summary"
        return normalized or "unknown"


class ManuscriptQaReport(BaseModel):
    overall_verdict: str = "Manuscript QA completed; no overall verdict was provided by the model."
    strengths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    continuity_risks: list[str] = Field(default_factory=list)
    repetition_risks: list[str] = Field(default_factory=list)
    ending_coherence_notes: list[str] = Field(default_factory=list)
    lint_findings: list[str] = Field(default_factory=list)
    chapter_ending_quality_notes: list[str] = Field(default_factory=list)
    easy_win_warnings: list[str] = Field(default_factory=list)
    proper_noun_continuity_findings: list[str] = Field(default_factory=list)
    side_character_agency_notes: list[str] = Field(default_factory=list)
    atmospheric_repetition_findings: list[str] = Field(default_factory=list)
    emotional_pacing_notes: list[str] = Field(default_factory=list)
    ideology_consistency_findings: list[str] = Field(default_factory=list)
    civilian_texture_findings: list[str] = Field(default_factory=list)
    technical_escalation_fatigue_findings: list[str] = Field(default_factory=list)
    crisis_loop_findings: list[str] = Field(default_factory=list)
    scene_mode_distribution_notes: list[str] = Field(default_factory=list)
    story_turn_quality_notes: list[str] = Field(default_factory=list)
    genre_contract_notes: list[str] = Field(default_factory=list)
    continuity_bible_findings: list[str] = Field(default_factory=list)
    continuity_bible_table: list[ContinuityBibleRow] = Field(default_factory=list)

    @field_validator("overall_verdict", mode="before")
    @classmethod
    def validate_overall_verdict(cls, value: Any) -> str:
        rendered = str(value or "").strip()
        return rendered or "Manuscript QA completed; no overall verdict was provided by the model."

    @field_validator(
        "strengths",
        "warnings",
        "continuity_risks",
        "repetition_risks",
        "ending_coherence_notes",
        "lint_findings",
        "chapter_ending_quality_notes",
        "easy_win_warnings",
        "proper_noun_continuity_findings",
        "side_character_agency_notes",
        "atmospheric_repetition_findings",
        "emotional_pacing_notes",
        "ideology_consistency_findings",
        "civilian_texture_findings",
        "technical_escalation_fatigue_findings",
        "crisis_loop_findings",
        "scene_mode_distribution_notes",
        "story_turn_quality_notes",
        "genre_contract_notes",
        "continuity_bible_findings",
        mode="before",
    )
    @classmethod
    def validate_report_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("continuity_bible_table", mode="before")
    @classmethod
    def validate_continuity_bible_table(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, ContinuityBibleRow):
            return [value.model_dump()]
        if isinstance(value, BaseModel):
            return [value.model_dump()]
        if isinstance(value, str):
            rendered = value.strip()
            return [{"item_type": "note", "name": rendered, "notes": rendered}] if rendered else []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            rows: list[Any] = []
            for item in value:
                if isinstance(item, ContinuityBibleRow):
                    rows.append(item.model_dump())
                elif isinstance(item, BaseModel):
                    rows.append(item.model_dump())
                elif isinstance(item, dict):
                    rows.append(item)
                elif str(item).strip():
                    rendered = str(item).strip()
                    rows.append({"item_type": "note", "name": rendered, "notes": rendered})
            return rows
        return []


class DevelopmentalChapterAction(BaseModel):
    chapter_numbers: list[int] = Field(default_factory=list)
    action: str = "rewrite"
    reason: str = ""
    required_story_change: str = ""
    permanent_consequence: str = ""

    @field_validator("chapter_numbers", mode="before")
    @classmethod
    def validate_chapter_numbers(cls, value: Any) -> list[int]:
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            value = [item.strip() for item in value.replace(",", "\n").splitlines()]
        if isinstance(value, list):
            numbers: list[int] = []
            for item in value:
                try:
                    number = int(str(item).strip())
                except ValueError:
                    continue
                if number > 0:
                    numbers.append(number)
            return list(dict.fromkeys(numbers))
        return []

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, value: Any) -> str:
        action = str(value or "rewrite").strip().lower().replace("-", "_").replace(" ", "_")
        return action or "rewrite"

    @field_validator("reason", "required_story_change", "permanent_consequence", mode="before")
    @classmethod
    def validate_action_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class DevelopmentalRewritePlan(BaseModel):
    overall_diagnosis: str = "Developmental rewrite assessment completed."
    act_structure_notes: list[str] = Field(default_factory=list)
    chapter_actions: list[DevelopmentalChapterAction] = Field(default_factory=list)
    merge_candidates: list[str] = Field(default_factory=list)
    cut_candidates: list[str] = Field(default_factory=list)
    continuity_repairs: list[str] = Field(default_factory=list)
    theme_arc_repairs: list[str] = Field(default_factory=list)
    prose_pattern_repairs: list[str] = Field(default_factory=list)
    pre_rewrite_risks: list[str] = Field(default_factory=list)
    post_rewrite_risk_targets: list[str] = Field(default_factory=list)

    @field_validator("overall_diagnosis", mode="before")
    @classmethod
    def validate_overall_diagnosis(cls, value: Any) -> str:
        rendered = str(value or "").strip()
        return rendered or "Developmental rewrite assessment completed."

    @field_validator(
        "act_structure_notes",
        "merge_candidates",
        "cut_candidates",
        "continuity_repairs",
        "theme_arc_repairs",
        "prose_pattern_repairs",
        "pre_rewrite_risks",
        "post_rewrite_risk_targets",
        mode="before",
    )
    @classmethod
    def validate_plan_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)


class TaskRouteOverride(BaseModel):
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)

    @field_validator("provider_name", "model_name", mode="before")
    @classmethod
    def validate_route_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class TaskRouting(BaseModel):
    story_bible: TaskRouteOverride | None = None
    outline: TaskRouteOverride | None = None
    chapter_plan: TaskRouteOverride | None = None
    chapter_draft: TaskRouteOverride | None = None
    chapter_critique: TaskRouteOverride | None = None
    chapter_revision: TaskRouteOverride | None = None
    chapter_summary: TaskRouteOverride | None = None
    continuity_update: TaskRouteOverride | None = None
    manuscript_qa: TaskRouteOverride | None = None
    developmental_rewrite: TaskRouteOverride | None = None


class ProviderCapabilities(BaseModel):
    provider_name: str = "ollama"
    reachable: bool
    base_url: str
    default_model: str
    available_models: list[str] = Field(default_factory=list)
    error: str | None = None


class ProviderConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_name: str
    base_url: str
    default_model: str
    api_key_set: bool = False
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ProviderConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    base_url: str = Field(min_length=1)
    default_model: str = Field(min_length=1)
    api_key: str | None = None
    is_enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("Enter a full provider URL starting with http:// or https://.")
        return value.rstrip("/")

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: Any) -> str | None:
        if value is None:
            return None
        rendered = str(value).strip()
        return rendered or None


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1)
    premise: str = Field(min_length=1)
    desired_word_count: int = Field(ge=1)
    requested_chapters: int = Field(ge=1)
    min_words_per_chapter: int = Field(ge=1)
    max_words_per_chapter: int = Field(ge=1)
    preferred_provider_name: str = Field(min_length=1, default="ollama")
    preferred_model: str = Field(min_length=1)
    notes: str | None = None
    story_brief: StoryBrief = Field(default_factory=StoryBrief)
    task_routing: TaskRouting = Field(default_factory=TaskRouting)

    @model_validator(mode="after")
    def validate_ranges(self) -> "ProjectCreate":
        if self.max_words_per_chapter < self.min_words_per_chapter:
            raise ValueError("Max words per chapter must be greater than or equal to min words per chapter.")
        return self


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = None
    premise: str | None = None
    desired_word_count: int | None = Field(default=None, ge=1)
    requested_chapters: int | None = Field(default=None, ge=1)
    min_words_per_chapter: int | None = Field(default=None, ge=1)
    max_words_per_chapter: int | None = Field(default=None, ge=1)
    preferred_provider_name: str | None = None
    preferred_model: str | None = None
    notes: str | None = None
    story_brief: StoryBrief | None = None
    task_routing: TaskRouting | None = None


class ChapterDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_number: int
    title: str
    outline_summary: str
    plan: str | None
    content: str | None
    summary: str | None
    continuity_update: ChapterContinuityUpdate | None = None
    qa_notes: ChapterCritique | None = None
    status: ChapterStatus
    word_count: int
    error_message: str | None


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    filename: str
    relative_path: str
    content_type: str
    created_at: datetime


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    event_type: str
    payload: dict
    created_at: datetime


class RunStageAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    run_id: str
    stage: str
    chapter_number: int | None
    attempt_number: int
    provider_name: str
    model_name: str
    status: str
    error_type: str | None
    error_message: str | None
    duration_ms: int | None
    output_chars: int
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="attempt_metadata")
    started_at: datetime
    completed_at: datetime | None


class RunCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    project_id: str
    provider_name: str | None = None
    model_name: str | None = None
    target_word_count: int | None = Field(default=None, ge=1)
    requested_chapters: int | None = Field(default=None, ge=1)
    min_words_per_chapter: int | None = Field(default=None, ge=1)
    max_words_per_chapter: int | None = Field(default=None, ge=1)
    pause_after_outline: bool = True
    developmental_rewrite_enabled: bool = True
    quality_profile: str = "balanced"
    task_routing: TaskRouting | None = None
    source_run_id: str | None = None
    resume_from_chapter: int | None = Field(default=None, ge=1)

    @field_validator("quality_profile", mode="before")
    @classmethod
    def validate_quality_profile(cls, value: Any) -> str:
        return _validate_quality_profile(value)


class GenerationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    source_run_id: str | None
    provider_name: str
    model_name: str
    target_word_count: int
    requested_chapters: int
    min_words_per_chapter: int
    max_words_per_chapter: int
    pipeline_version: int
    pause_after_outline: bool
    developmental_rewrite_enabled: bool
    quality_profile: str
    status: RunStatus
    current_step: str
    current_chapter: int | None
    outline: list[dict[str, Any]] | None = None
    story_bible: StoryBible | None = None
    continuity_ledger: ContinuityLedger | None = None
    task_routing: TaskRouting = Field(default_factory=TaskRouting)
    summary_context: str | None
    error_message: str | None
    cancel_requested: bool
    resume_from_chapter: int | None
    worker_id: str | None
    last_heartbeat_at: datetime | None
    recovery_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    chapters: list[ChapterDraftRead] = Field(default_factory=list)
    artifacts: list[ArtifactRead] = Field(default_factory=list)
    events: list[RunEventRead] = Field(default_factory=list)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    premise: str
    desired_word_count: int
    requested_chapters: int
    min_words_per_chapter: int
    max_words_per_chapter: int
    preferred_provider_name: str
    preferred_model: str
    notes: str | None
    story_brief: StoryBrief = Field(default_factory=StoryBrief)
    task_routing: TaskRouting = Field(default_factory=TaskRouting)
    created_at: datetime
    updated_at: datetime
    runs: list[GenerationRunRead] = Field(default_factory=list)
