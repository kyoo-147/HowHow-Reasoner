"""Versioned, serializable Phase 0 domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "v1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    schema_version: str = SCHEMA_VERSION
    _version: ClassVar[str] = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value: str) -> str:
        if value != cls._version:
            raise ValueError(f"unsupported schema version: {value}")
        return value


class ID(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class Correlation(Contract):
    project_id: ID
    episode_id: ID | None = None
    task_id: ID | None = None
    run_id: ID | None = None
    artifact_id: ID | None = None


class Actor(Contract):
    actor_id: ID
    kind: str = Field(min_length=1)
    display_name: str | None = None


class ProviderIdentity(Contract):
    provider_id: str = Field(min_length=1)
    provider_kind: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    version: str = Field(min_length=1)


class Lifecycle(StrEnum):
    INTAKE = "INTAKE"
    BRIEFING = "BRIEFING"
    SCOPING = "SCOPING"
    LITERATURE = "LITERATURE"
    CANDIDATES = "CANDIDATES"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    BASELINE = "BASELINE"
    EXPERIMENTING = "EXPERIMENTING"
    ANALYZING = "ANALYZING"
    WRITING = "WRITING"
    REVIEW = "REVIEW"
    REPRODUCIBILITY = "REPRODUCIBILITY"
    PACKAGING = "PACKAGING"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class RecordStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"
    WITHDRAWN = "WITHDRAWN"


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    REVISE = "REVISE"
    REJECTED = "REJECTED"


class GateStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_LIMITATIONS = "PASS_WITH_LIMITATIONS"
    REVISE = "REVISE"
    BLOCK = "BLOCK"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class Timestamped(Contract):
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class ResearchBrief(Timestamped):
    brief_id: ID
    project_id: ID
    question: str
    lifecycle: Lifecycle = Lifecycle.BRIEFING
    scope: list[str] = []
    constraints: list[str] = []
    budget: BudgetReservation | None = None


class ApprovalRecord(Timestamped):
    approval_id: ID
    actor: Actor
    scope: str
    status: RecordStatus = RecordStatus.APPROVED
    expires_at: datetime | None = None


class BudgetReservation(Contract):
    reservation_id: ID
    resource: str
    limit: float = Field(ge=0)
    spent: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def within_limit(self) -> Self:
        if self.spent > self.limit:
            raise ValueError("spent cannot exceed limit")
        return self


class PolicySnapshot(Timestamped):
    policy_id: ID
    name: str
    permissions: list[str] = []
    allow_network: bool = False
    hash: str = Field(min_length=1)


class TaskSpec(Timestamped):
    task_id: ID
    project_id: ID
    objective: str
    provider: ProviderIdentity
    budget: BudgetReservation | None = None
    inputs: list[ID] = []
    idempotency_key: str = Field(min_length=1)
    status: RecordStatus = RecordStatus.PROPOSED


class TaskFailure(Contract):
    code: str
    message: str
    retryable: bool = False
    evidence: list[ID] = []


class TaskResult(Timestamped):
    task_id: ID
    attempt: int = Field(ge=1)
    status: RecordStatus
    provider: ProviderIdentity
    artifacts: list[ID] = []
    failure: TaskFailure | None = None
    resource_usage: dict[str, float] = {}


class LeaseRecord(Timestamped):
    lease_id: ID
    task_id: ID
    provider: ProviderIdentity
    attempt: int = Field(ge=1)
    fencing_token: str
    expires_at: datetime


class ProviderCapabilities(Contract):
    provider: ProviderIdentity
    task_kinds: list[str] = []
    concurrency: int = Field(ge=1)
    sandbox: str
    network: str


class ProviderReadiness(Contract):
    provider: ProviderIdentity
    status: str
    checked_at: datetime
    evidence: list[ID] = []

    @field_validator("checked_at")
    @classmethod
    def ready_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class ProviderHandle(Contract):
    provider: ProviderIdentity
    handle: str = Field(min_length=1)
    attempt: int = Field(ge=1)


class SourceRecord(Timestamped):
    source_id: ID
    provider: str
    stable_locator: str
    content_sha256: str
    access: str


class EvidenceSpan(Contract):
    evidence_id: ID
    source_id: ID
    locator: str
    quote: str
    status: EvidenceStatus = EvidenceStatus.UNVERIFIED


class Hypothesis(Contract):
    hypothesis_id: ID
    statement: str
    assumptions: list[str] = []
    falsifier: str
    status: RecordStatus = RecordStatus.PROPOSED


class Decision(Timestamped):
    decision_id: ID
    actor: Actor
    choice: str
    rationale: str
    alternatives: list[str] = []


class ClaimRecord(Contract):
    claim_id: ID
    wording: str
    claim_type: str
    status: ClaimStatus = ClaimStatus.UNRESOLVED
    evidence: list[ID] = []
    limitations: list[str] = []


class RunManifest(Timestamped):
    run_id: ID
    task_id: ID
    code_revision: str
    command: list[str]
    seed: int | None = None
    status: RecordStatus
    artifacts: list[ID] = []
    completed: bool = False


class MetricRecord(Contract):
    metric_id: ID
    run_id: ID
    name: str
    value: float
    unit: str | None = None


class ArtifactManifest(Timestamped):
    artifact_id: ID
    media_type: str
    content_sha256: str
    producer: str
    portable_path: str
    parents: list[ID] = []
    completed: bool = False


class ReviewRecord(Timestamped):
    review_id: ID
    reviewer: Actor
    status: ReviewStatus
    findings: list[str] = []
    dissent: list[str] = []


class GateReport(Timestamped):
    gate_id: ID
    name: str
    status: GateStatus
    findings: list[str] = []


class PackageManifest(Timestamped):
    package_id: ID
    artifacts: list[ID]
    gates: list[ID]
    label: str = "READY FOR HUMAN REVIEW"
    completed: bool = False


class EventEnvelope(Timestamped):
    event_id: ID
    actor: Actor
    event_type: str
    aggregate_type: str
    aggregate_id: ID
    correlation: Correlation
    payload: dict[str, Any]
    payload_sha256: str
    previous_event_sha256: str | None = None

    @model_validator(mode="after")
    def verify_payload(self) -> Self:
        actual = (
            sha256(self.model_dump_json(exclude={"payload_sha256"}).encode()).hexdigest()
            if False
            else sha256(
                __import__("json")
                .dumps(self.payload, sort_keys=True, separators=(",", ":"))
                .encode()
            ).hexdigest()
        )
        if actual != self.payload_sha256:
            raise ValueError("payload_sha256 does not match payload")
        return self


RECORD_TYPES: list[type[BaseModel]] = [
    ResearchBrief,
    ApprovalRecord,
    BudgetReservation,
    PolicySnapshot,
    TaskSpec,
    TaskResult,
    TaskFailure,
    LeaseRecord,
    ProviderCapabilities,
    ProviderReadiness,
    ProviderHandle,
    SourceRecord,
    EvidenceSpan,
    Hypothesis,
    Decision,
    ClaimRecord,
    RunManifest,
    MetricRecord,
    ArtifactManifest,
    ReviewRecord,
    GateReport,
    PackageManifest,
    EventEnvelope,
]
