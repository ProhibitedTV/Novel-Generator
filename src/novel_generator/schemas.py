from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import ChapterStatus, RunStatus


class ProviderCapabilities(BaseModel):
    provider_name: str = "ollama"
    reachable: bool
    base_url: str
    default_model: str
    available_models: list[str] = Field(default_factory=list)
    error: str | None = None


class ProviderConfigUpdate(BaseModel):
    base_url: str
    default_model: str


class ProjectCreate(BaseModel):
    title: str
    premise: str
    desired_word_count: int = Field(ge=1)
    requested_chapters: int = Field(ge=1)
    min_words_per_chapter: int = Field(ge=1)
    max_words_per_chapter: int = Field(ge=1)
    preferred_model: str
    notes: str | None = None


class ChapterDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_number: int
    title: str
    outline_summary: str
    plan: str | None
    content: str | None
    summary: str | None
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
    project_id: str
    model_name: str | None = None
    target_word_count: int | None = Field(default=None, ge=1)
    requested_chapters: int | None = Field(default=None, ge=1)
    min_words_per_chapter: int | None = Field(default=None, ge=1)
    max_words_per_chapter: int | None = Field(default=None, ge=1)
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
    status: RunStatus
    current_step: str
    current_chapter: int | None
    outline: list[dict] | None = None
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
    created_at: datetime
    updated_at: datetime
    runs: list[GenerationRunRead] = Field(default_factory=list)
