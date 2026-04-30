from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import ChapterStatus, RunStatus


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.splitlines()]
        return [item for item in items if item]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


class StoryBrief(BaseModel):
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

    @field_validator("supporting_cast", "world_rules", "must_include", "avoid", mode="before")
    @classmethod
    def validate_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("setting", "tone", "protagonist", "antagonist", "core_conflict", "ending_target", mode="before")
    @classmethod
    def validate_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class StoryCastMember(BaseModel):
    name: str
    role: str
    desire: str
    risk: str


class StoryBible(BaseModel):
    logline: str
    theme: str
    act_plan: list[str] = Field(default_factory=list)
    cast: list[StoryCastMember] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    core_system_rules: list[str] = Field(default_factory=list)
    ending_promise: str


class StructuredOutlineEntry(BaseModel):
    chapter_number: int = Field(ge=1)
    act: str
    title: str
    objective: str
    conflict_turn: str
    character_turn: str
    reveal: str
    ending_state: str


class ContinuityLedger(BaseModel):
    current_patch_status: str
    character_states: dict[str, str] = Field(default_factory=dict)
    world_state: str
    open_threads: list[str] = Field(default_factory=list)
    resolved_threads: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)


class ChapterPlan(BaseModel):
    opening_state: str
    character_goal: str
    scene_beats: list[str] = Field(default_factory=list)
    conflict_turn: str
    ending_hook: str


class ChapterContinuityUpdate(BaseModel):
    chapter_outcome: str
    current_patch_status: str
    character_states: dict[str, str] = Field(default_factory=dict)
    world_state: str
    open_threads: list[str] = Field(default_factory=list)
    resolved_threads: list[str] = Field(default_factory=list)
    timeline_entry: str
    timeline: list[str] = Field(default_factory=list)


class ChapterCritique(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    revision_required: bool = False
    focus: list[str] = Field(default_factory=list)


class ManuscriptQaReport(BaseModel):
    overall_verdict: str
    strengths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    continuity_risks: list[str] = Field(default_factory=list)
    repetition_risks: list[str] = Field(default_factory=list)
    ending_coherence_notes: list[str] = Field(default_factory=list)
    lint_findings: list[str] = Field(default_factory=list)


class ProviderCapabilities(BaseModel):
    provider_name: str = "ollama"
    reachable: bool
    base_url: str
    default_model: str
    available_models: list[str] = Field(default_factory=list)
    error: str | None = None


class ProviderConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    base_url: str = Field(min_length=1)
    default_model: str = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("Enter a full Ollama URL starting with http:// or https://.")
        return value.rstrip("/")


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1)
    premise: str = Field(min_length=1)
    desired_word_count: int = Field(ge=1)
    requested_chapters: int = Field(ge=1)
    min_words_per_chapter: int = Field(ge=1)
    max_words_per_chapter: int = Field(ge=1)
    preferred_model: str = Field(min_length=1)
    notes: str | None = None
    story_brief: StoryBrief = Field(default_factory=StoryBrief)

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
    preferred_model: str | None = None
    notes: str | None = None
    story_brief: StoryBrief | None = None


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


class RunCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    project_id: str
    model_name: str | None = None
    target_word_count: int | None = Field(default=None, ge=1)
    requested_chapters: int | None = Field(default=None, ge=1)
    min_words_per_chapter: int | None = Field(default=None, ge=1)
    max_words_per_chapter: int | None = Field(default=None, ge=1)
    pause_after_outline: bool = True
    source_run_id: str | None = None
    resume_from_chapter: int | None = Field(default=None, ge=1)


class GenerationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    source_run_id: str | None
    model_name: str
    target_word_count: int
    requested_chapters: int
    min_words_per_chapter: int
    max_words_per_chapter: int
    pipeline_version: int
    pause_after_outline: bool
    status: RunStatus
    current_step: str
    current_chapter: int | None
    outline: list[dict[str, Any]] | None = None
    story_bible: StoryBible | None = None
    continuity_ledger: ContinuityLedger | None = None
    summary_context: str | None
    error_message: str | None
    cancel_requested: bool
    resume_from_chapter: int | None
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
    preferred_model: str
    notes: str | None
    story_brief: StoryBrief = Field(default_factory=StoryBrief)
    created_at: datetime
    updated_at: datetime
    runs: list[GenerationRunRead] = Field(default_factory=list)
