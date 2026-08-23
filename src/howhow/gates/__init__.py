"""Evidence-first package completeness gates."""

from .evaluate import ReadinessReport, evaluate_readiness, package_fingerprint
from .models import (
    ArtifactLineage,
    CitationSpan,
    ClaimEvidence,
    CompileCheck,
    LicenseManifest,
    Outcome,
    PackageInput,
    ReproducibilityManifest,
    RunRecord,
    StatisticalPlan,
)

__all__ = [
    "ArtifactLineage",
    "CitationSpan",
    "ClaimEvidence",
    "CompileCheck",
    "LicenseManifest",
    "Outcome",
    "PackageInput",
    "ReadinessReport",
    "ReproducibilityManifest",
    "RunRecord",
    "StatisticalPlan",
    "evaluate_readiness",
    "package_fingerprint",
]
