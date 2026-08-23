"""Small, deterministic evidence graph types; retrieved text is never executable."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

_HEX = re.compile(r"^[0-9a-f]{64}$")


class AccessStatus(StrEnum):
    OPEN = "open"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    provider: str
    stable_id: str
    version: str | None
    access: AccessStatus
    retrieved_at: datetime
    raw_sha256: str
    title: str = ""
    url: str | None = None
    license: str | None = None
    abstract: str = ""

    def __post_init__(self) -> None:
        if not self.source_id or not self.stable_id or not self.provider:
            raise ValueError("source identity fields are required")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if not _HEX.fullmatch(self.raw_sha256):
            raise ValueError("raw_sha256 must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    evidence_id: str
    source_id: str
    locator: str
    quote: str
    status: EvidenceStatus = EvidenceStatus.UNVERIFIED
    kind: str = "text"

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_id or not self.locator or not self.quote:
            raise ValueError("evidence span requires an exact locator and non-empty quote")
        if self.kind == "prose":
            raise ValueError("prose-only results cannot be evidence")

    @classmethod
    def from_text(
        cls,
        *,
        evidence_id: str,
        source_id: str,
        text: str,
        start: int,
        end: int,
        locator: str,
        status: EvidenceStatus = EvidenceStatus.UNVERIFIED,
    ) -> EvidenceSpan:
        if start < 0 or end <= start or end > len(text):
            raise ValueError("span offsets are outside the retrieved text")
        quote = text[start:end]
        if not quote.strip():
            raise ValueError("evidence quote cannot be blank")
        return cls(evidence_id, source_id, locator, quote, status)


@dataclass(frozen=True, slots=True)
class ContradictionEdge:
    source_evidence_id: str
    target_evidence_id: str
    reason: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.reason.strip() or not 0 <= self.confidence <= 1:
            raise ValueError("contradiction edge requires a bounded reason/confidence")


@dataclass(frozen=True, slots=True)
class ClaimSupportAudit:
    claim_id: str
    supported: tuple[str, ...] = ()
    contradicted: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    status: str = "UNRESOLVED"

    def __post_init__(self) -> None:
        if not self.supported and not self.contradicted and self.status == "SUPPORTED":
            raise ValueError("a supported claim must cite evidence")


def deduplication_key(source: SourceRecord) -> tuple[str, str, str]:
    return (source.provider.lower(), source.stable_id.lower(), source.version or "")


def deduplicate_sources(sources: Iterable[SourceRecord]) -> tuple[SourceRecord, ...]:
    """Keep first-seen records, while allowing different versions of one work."""
    seen: set[tuple[str, str, str]] = set()
    result: list[SourceRecord] = []
    for source in sources:
        key = deduplication_key(source)
        if key not in seen:
            seen.add(key)
            result.append(source)
    return tuple(result)


def resolve_identity(sources: Iterable[SourceRecord]) -> dict[str, tuple[str, ...]]:
    """Group provider records by normalized DOI/arXiv/OpenAlex/S2 identity."""
    groups: dict[str, list[str]] = {}
    for source in sources:
        value = source.stable_id.lower().strip()
        value = re.sub(r"^(https?://(doi.org/|arxiv.org/))", "", value)
        key = f"doi:{value}" if value.startswith("10.") else value
        groups.setdefault(key, []).append(source.source_id)
    return {key: tuple(ids) for key, ids in groups.items()}


def audit_claim_support(
    claim_id: str,
    evidence: Mapping[str, EvidenceSpan],
    support_ids: Iterable[str],
    contradiction_ids: Iterable[str] = (),
) -> ClaimSupportAudit:
    supported = tuple(
        i for i in support_ids if i in evidence and evidence[i].status == EvidenceStatus.VERIFIED
    )
    contradicted = tuple(
        i
        for i in contradiction_ids
        if i in evidence and evidence[i].status != EvidenceStatus.REJECTED
    )
    if supported and contradicted:
        status = "CONTRADICTED"
    elif supported:
        status = "SUPPORTED"
    elif contradicted:
        status = "CONTRADICTED"
    else:
        status = "UNRESOLVED"
    limitations = (
        () if status != "UNRESOLVED" else ("No verified exact source span supports this claim.",)
    )
    return ClaimSupportAudit(claim_id, supported, contradicted, limitations, status)


def raw_hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def retrieved_now() -> datetime:
    return datetime.now(UTC)
