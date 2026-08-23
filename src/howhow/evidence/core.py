"""Evidence records. Retrieved source text is untrusted data and never executable."""

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
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None

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


def deduplication_key(source: SourceRecord) -> tuple[str, str, str]:
    return (source.provider.lower(), source.stable_id.lower(), source.version or "")


def deduplicate_sources(sources: Iterable[SourceRecord]) -> tuple[SourceRecord, ...]:
    seen = set()
    out = []
    for source in sources:
        if (key := deduplication_key(source)) not in seen:
            seen.add(key)
            out.append(source)
    return tuple(out)


def resolve_identity(sources: Iterable[SourceRecord]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = {}
    for source in sources:
        ids = [
            source.doi,
            source.arxiv_id,
            source.openalex_id,
            source.semantic_scholar_id,
            source.stable_id,
        ]
        keys = []
        for value in ids:
            if value:
                value = re.sub(
                    r"^(https?://(doi.org/|arxiv.org/|openalex.org/))", "", value.lower().strip()
                )
                keys.append("doi:" + value if value.startswith("10.") else value)
        key = min(keys) if keys else source.stable_id.lower()
        groups.setdefault(key, []).append(source.source_id)
    return {key: tuple(ids) for key, ids in groups.items()}


def audit_claim_support(
    claim_id: str,
    evidence: Mapping[str, EvidenceSpan],
    support_ids: Iterable[str],
    contradiction_ids: Iterable[str] = (),
) -> ClaimSupportAudit:
    supported = tuple(
        i for i in support_ids if i in evidence and evidence[i].status is EvidenceStatus.VERIFIED
    )
    contradicted = tuple(
        i
        for i in contradiction_ids
        if i in evidence and evidence[i].status is not EvidenceStatus.REJECTED
    )
    status = "CONTRADICTED" if contradicted else "SUPPORTED" if supported else "UNRESOLVED"
    return ClaimSupportAudit(
        claim_id,
        supported,
        contradicted,
        () if status != "UNRESOLVED" else ("No verified exact source span supports this claim.",),
        status,
    )


@dataclass(frozen=True, slots=True)
class ClaimSupportAudit:
    claim_id: str
    supported: tuple[str, ...] = ()
    contradicted: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    status: str = "UNRESOLVED"


def raw_hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def retrieved_now() -> datetime:
    return datetime.now(UTC)
