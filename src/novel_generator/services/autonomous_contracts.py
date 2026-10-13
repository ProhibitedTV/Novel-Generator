from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, StrictInt, model_validator


class PassageEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    text: str


class CoordinatedEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[PassageEdit] = Field(min_length=1, max_length=16)


class SourceParagraphReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: StrictInt = Field(ge=1)
    paragraph_number: StrictInt = Field(ge=1)


class EditorialIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_number: int = Field(ge=1)
    category: Literal["continuity", "causality", "character", "factual", "premature_payoff", "unresolved_payoff", "repetition", "prose", "length"]
    problem: str = Field(min_length=1)
    evidence: str = Field(min_length=1, description="Use 'Source paragraphs' when evidence_paragraphs is provided. Otherwise give an exact short quotation from this chapter's actual prose, not its metadata.")
    evidence_paragraphs: list[int] = Field(default_factory=list, description="Paragraph numbers ONLY within the chapter_number being repaired. Never put chapter numbers here. Use an empty list for chapter-pair reviews and source_references instead.")
    source_references: list[SourceParagraphReference] = Field(default_factory=list, description="For chapter-pair reviews, cite both conflicting source chapters with explicit chapter_number and paragraph_number objects. These are citations, not instructions to edit both chapters. Leave empty for a single-chapter review.")
    _source_evidence: list[dict] = PrivateAttr(default_factory=list)
    repair_instruction: str = Field(min_length=1)
    repair_scope: Literal["passage", "chapter"] = Field(default="passage", description="Use chapter for missing sections, broken organization, or a missing major promised outcome that cannot be fixed within the cited passages. Use passage only for localized defects. Evidence anchors a diagnosis; it does not restrict a structural repair to that paragraph.")


class EditorialReview(BaseModel):
    """No optimistic defaults: a missing check is an invalid review, never a pass."""
    model_config = ConfigDict(extra="forbid")

    reviewed_chapters: list[int] = Field(min_length=1)
    plot_coherent: StrictBool
    canon_consistent: StrictBool
    prose_clean: StrictBool
    ending_complete: StrictBool
    issues: list[EditorialIssue] = Field(description="Concrete defects requiring correction: contradictions, missing required events, broken grammar, or actual redundant passages. Do not include optional stylistic preferences.")
    optional_suggestions: list[EditorialIssue] = Field(default_factory=list, description="Evidence-grounded aesthetic preferences or optional refinements. These do not fail checks or require another repair.")
    _rejected_optional_suggestions: list[dict] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def require_actionable_failures(self):
        if not all((self.plot_coherent, self.canon_consistent, self.prose_clean, self.ending_complete)) and not self.issues:
            raise ValueError("Every failed check needs an evidenced issue and repair instruction.")
        # Keep failed checks false, but allow grounded repairs to proceed even
        # when the reviewer classifies an ending defect as repetition/prose.
        # Forcing a new category here encouraged invented closure requirements.
        return self

    @property
    def passed(self) -> bool:
        return not self.issues and all((self.plot_coherent, self.canon_consistent, self.prose_clean, self.ending_complete))
