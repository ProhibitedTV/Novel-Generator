"""Source-referenced contracts for complete book planning."""
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class OutlineSpineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chapter_number: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=280)
    ending_state: str = Field(min_length=1, max_length=280)
    new_progress: str = Field(min_length=1, max_length=280)


class OutlineProgressionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chapter_numbers: list[StrictInt] = Field(min_length=1, description="Source outline entries demonstrating the defect, in book order.")
    problem: str = Field(min_length=1)
    repair_instruction: str = Field(min_length=1)


class OutlineProgressionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed_chapters: list[StrictInt] = Field(min_length=1)
    progression_coherent: StrictBool
    issues: list[OutlineProgressionIssue]

    @model_validator(mode="after")
    def require_evidenced_failure(self):
        if not self.progression_coherent and not self.issues:
            raise ValueError("A failed progression check requires a source-referenced defect and repair instruction.")
        return self

    @property
    def passed(self):
        return self.progression_coherent and not self.issues
