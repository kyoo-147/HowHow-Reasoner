"""Typed, immutable review records bound to one package input fingerprint."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class FindingSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    NONBLOCKING = "NONBLOCKING"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    REVISE = "REVISE"
    REJECTED = "REJECTED"


class ReviewAction(StrEnum):
    RESOLVE = "RESOLVE"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    OVERRIDE = "OVERRIDE"


class ReviewFinding(ImmutableModel):
    finding_id: str = Field(min_length=1)
    severity: FindingSeverity
    message: str = Field(min_length=1)
    dissent: bool = False


class ReviewerRecord(ImmutableModel):
    review_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    status: ReviewStatus
    findings: tuple[ReviewFinding, ...] = ()
    independent: bool = True
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def unique_findings(self) -> ReviewerRecord:
        ids = [item.finding_id for item in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("review finding ids must be unique")
        return self


class ReviewResponse(ImmutableModel):
    response_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    action: ReviewAction
    reason: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReviewOverride(ImmutableModel):
    override_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    authorized_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
