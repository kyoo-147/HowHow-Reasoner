"""Input contracts for deterministic, package-completeness evaluation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from howhow.review.models import ReviewerRecord, ReviewOverride, ReviewResponse


class GateModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Outcome(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"


class CitationSpan(GateModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    source_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def exact(self) -> CitationSpan:
        if self.end <= self.start or self.end > len(self.source_text):
            raise ValueError("citation offsets are outside source text")
        if self.source_text[self.start : self.end] != self.quote:
            raise ValueError("citation quote does not match its exact source span")
        return self


class ClaimEvidence(GateModel):
    claim_id: str = Field(min_length=1)
    wording: str = Field(min_length=1)
    citations: tuple[CitationSpan, ...] = ()
    result_only: bool = False


class ArtifactLineage(GateModel):
    artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    parent_artifact_ids: tuple[str, ...] = ()


class RunRecord(GateModel):
    run_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    artifact_ids: tuple[str, ...] = ()
    failure_preserved: bool = False


class StatisticalPlan(GateModel):
    content: str = Field(min_length=1)


class ReproducibilityManifest(GateModel):
    code_revision: str = Field(min_length=1)
    command: tuple[str, ...] = Field(min_length=1)
    environment: tuple[tuple[str, str], ...] = ()
    seed: int | None = None


class LicenseManifest(GateModel):
    entries: tuple[str, ...] = ()


class CompileCheck(GateModel):
    passed: bool
    command: str = Field(min_length=1)


class PackageInput(GateModel):
    claims: tuple[ClaimEvidence, ...] = ()
    lineage: tuple[ArtifactLineage, ...] = ()
    runs: tuple[RunRecord, ...] = ()
    statistical_plan: StatisticalPlan | None = None
    reproducibility: ReproducibilityManifest | None = None
    licenses: LicenseManifest | None = None
    compile_check: CompileCheck | None = None
    outcome: Outcome = Outcome.INCONCLUSIVE
    package_artifacts: tuple[str, ...] = ()
    reviewer_records: tuple[ReviewerRecord, ...] = ()
    responses: tuple[ReviewResponse, ...] = ()
    overrides: tuple[ReviewOverride, ...] = ()
