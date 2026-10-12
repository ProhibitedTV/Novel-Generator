"""Portable, source-grounded art direction for an accepted book."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoverMotif(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    description: str = Field(min_length=1, max_length=300)
    source_excerpt_id: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=3, max_length=240)


class TypographySafeZones(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=10, max_length=300)
    author: str = Field(min_length=10, max_length=300)


class CoverConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    title: str = Field(min_length=1, max_length=100)
    front_cover_prompt: str = Field(min_length=40, max_length=3200)
    negative_prompt: str = Field(min_length=10, max_length=700)
    typography_safe_zones: TypographySafeZones
    aspect_ratio: Literal["2:3", "3:4", "5:8"]
    visual_motif_ids: list[str] = Field(min_length=1, max_length=8)
    genre_signal_notes: str = Field(min_length=10, max_length=500)
    back_cover_background_prompt: str | None = Field(default=None, max_length=1200)


class CoverDesign(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    visual_motif_summary: str = Field(min_length=10, max_length=800)
    image_continuity_tags: list[str] = Field(min_length=1, max_length=12)
    motifs: list[CoverMotif] = Field(min_length=3, max_length=8)
    primary_concept_id: str
    concepts: list[CoverConcept] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def require_distinct_concepts_and_valid_links(self):
        motif_ids = [motif.id for motif in self.motifs]
        concept_ids = [concept.id for concept in self.concepts]
        if len(set(motif_ids)) != len(motif_ids) or len(set(concept_ids)) != len(concept_ids):
            raise ValueError("Motif and concept IDs must be unique.")
        if self.primary_concept_id not in concept_ids:
            raise ValueError("The primary concept must reference one of the supplied concepts.")
        prompts = [" ".join(concept.front_cover_prompt.casefold().split()) for concept in self.concepts]
        titles = [concept.title.casefold() for concept in self.concepts]
        if len(set(prompts)) != len(prompts) or len(set(titles)) != len(titles):
            raise ValueError("Cover concepts need distinct titles and front-cover prompts.")
        for concept in self.concepts:
            if len(set(concept.visual_motif_ids)) != len(concept.visual_motif_ids) or not set(concept.visual_motif_ids) <= set(motif_ids):
                raise ValueError("Every concept must reference unique, existing visual motifs.")
        return self


class CoverPromptPack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    run_id: str
    book_title: str
    manuscript_hash: str
    fingerprint: str
    provider_name: str
    model_name: str
    source_excerpts: list[dict]
    design: CoverDesign
