from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class EditorialIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_number: int = Field(ge=1)
    category: Literal["continuity", "causality", "character", "premature_payoff", "unresolved_payoff", "repetition", "prose", "length"]
    problem: str = Field(min_length=1)
    evidence: str = Field(min_length=1, description="Exact short quotation from this chapter's actual prose.")
    evidence_paragraphs: list[int] = Field(default_factory=list, description="Numbered source paragraphs supporting this issue. Prefer these references to copying dialogue.")
    repair_instruction: str = Field(min_length=1)


class EditorialReview(BaseModel):
    """No optimistic defaults: a missing check is an invalid review, never a pass."""
    model_config = ConfigDict(extra="forbid")

    reviewed_chapters: list[int] = Field(min_length=1)
    plot_coherent: StrictBool
    canon_consistent: StrictBool
    prose_clean: StrictBool
    ending_complete: StrictBool
    issues: list[EditorialIssue]

    @model_validator(mode="after")
    def require_actionable_failures(self):
        if not all((self.plot_coherent, self.canon_consistent, self.prose_clean, self.ending_complete)) and not self.issues:
            raise ValueError("Every failed check needs an evidenced issue and repair instruction.")
        categories = {issue.category for issue in self.issues}
        if not self.ending_complete and not categories.intersection({"unresolved_payoff", "premature_payoff", "causality"}):
            raise ValueError("A failed ending check needs an ending or causal issue, not an unrelated stylistic suggestion.")
        return self

    @property
    def passed(self) -> bool:
        return not self.issues and all((self.plot_coherent, self.canon_consistent, self.prose_clean, self.ending_complete))
