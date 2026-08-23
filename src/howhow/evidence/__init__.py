"""Read-only, provenance-preserving evidence primitives."""

from .core import (
    AccessStatus,
    ClaimSupportAudit,
    ContradictionEdge,
    EvidenceSpan,
    EvidenceStatus,
    SourceRecord,
    audit_claim_support,
    deduplicate_sources,
    resolve_identity,
)
from .retrieval import (
    CacheHook,
    FileCache,
    HttpResponse,
    RateClass,
    RetrievalError,
    classify_response,
    retrieve_json,
)

__all__ = [
    "AccessStatus",
    "CacheHook",
    "FileCache",
    "ClaimSupportAudit",
    "ContradictionEdge",
    "EvidenceSpan",
    "EvidenceStatus",
    "HttpResponse",
    "RateClass",
    "RetrievalError",
    "SourceRecord",
    "audit_claim_support",
    "classify_response",
    "deduplicate_sources",
    "resolve_identity",
    "retrieve_json",
]
