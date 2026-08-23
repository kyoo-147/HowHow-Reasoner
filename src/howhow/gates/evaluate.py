"""Deterministic readiness evaluation; this never assesses scientific truth."""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from howhow.review.models import ReviewAction

from .models import PackageInput


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    blocking: bool
    findings: tuple[str, ...] = ()


class ReadinessReport(BaseModel):
    """A package-completeness result, explicitly separate from scientific truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    status: str
    outcome: str
    scientific_truth: str = "NOT_EVALUATED"
    input_fingerprint: str
    gates: tuple[GateResult, ...]
    findings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.label == "READY FOR HUMAN REVIEW"


class _ReviewState(NamedTuple):
    findings: tuple[str, ...]
    blocking: bool


def package_fingerprint(package: PackageInput) -> str:
    """Hash only package inputs, so adding a review cannot hide input changes."""
    data = package.model_dump(mode="json", exclude={"reviewer_records", "responses", "overrides"})
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _gate(name: str, issues: list[str]) -> GateResult:
    return GateResult(
        name=name, passed=not issues, blocking=bool(issues), findings=tuple(sorted(issues))
    )


def _review_state(package: PackageInput, fingerprint: str) -> _ReviewState:
    issues: list[str] = []
    if not package.reviewer_records:
        issues.append("review: at least one independent reviewer record is required")
    if package.reviewer_records and not any(r.independent for r in package.reviewer_records):
        issues.append("review: an independent reviewer record is required")
    valid_findings = {}
    for record in package.reviewer_records:
        if record.input_fingerprint != fingerprint:
            issues.append(f"review: stale review {record.review_id} invalidated by changed inputs")
        for finding in record.findings:
            valid_findings[finding.finding_id] = finding
    resolved = {
        response.finding_id
        for response in package.responses
        if response.input_fingerprint == fingerprint
        and response.action in (ReviewAction.RESOLVE, ReviewAction.OVERRIDE)
        and response.reason.strip()
    }
    resolved.update(
        override.finding_id
        for override in package.overrides
        if override.input_fingerprint == fingerprint and override.reason.strip()
    )
    for response in package.responses:
        if response.input_fingerprint != fingerprint:
            issues.append(f"review: stale response {response.response_id} ignored")
    for override in package.overrides:
        if override.input_fingerprint != fingerprint:
            issues.append(f"review: stale override {override.override_id} ignored")
    for finding in valid_findings.values():
        if finding.finding_id not in resolved:
            prefix = "dissent" if finding.dissent else "finding"
            issues.append(
                f"review: unresolved {finding.severity.value.lower()} {prefix} {finding.finding_id}"
            )
    return _ReviewState(
        tuple(sorted(set(issues))), any("blocking" in i or "dissent" in i for i in issues)
    )


def evaluate_readiness(package: PackageInput) -> ReadinessReport:
    fingerprint = package_fingerprint(package)
    gates: list[GateResult] = []

    claim_issues: list[str] = []
    for claim in package.claims:
        if claim.result_only:
            claim_issues.append(
                f"claims: result-only claim {claim.claim_id} has no evidentiary basis"
            )
        if not claim.citations:
            claim_issues.append(f"claims: claim {claim.claim_id} lacks exact citation spans")
    gates.append(_gate("claim_coverage_and_exact_citations", claim_issues))

    lineage_issues: list[str] = []
    run_ids = {run.run_id for run in package.runs}
    artifact_ids = {artifact.artifact_id for artifact in package.lineage}
    for artifact in package.lineage:
        if artifact.run_id not in run_ids:
            lineage_issues.append(
                f"lineage: artifact {artifact.artifact_id} references missing run"
            )
        if any(parent not in artifact_ids for parent in artifact.parent_artifact_ids):
            lineage_issues.append(f"lineage: artifact {artifact.artifact_id} has missing parent")
    for run in package.runs:
        if run.status == "FAILED" and not run.failure_preserved:
            lineage_issues.append(f"lineage: failed run {run.run_id} was not preserved")
        if any(artifact not in artifact_ids for artifact in run.artifact_ids):
            lineage_issues.append(f"lineage: run {run.run_id} references missing artifact")
    gates.append(_gate("artifact_run_lineage_and_failed_runs", lineage_issues))

    gates.append(
        _gate(
            "statistical_plan",
            [] if package.statistical_plan else ["statistics: statistical plan is missing"],
        )
    )
    gates.append(
        _gate(
            "reproducibility_manifest",
            [] if package.reproducibility else ["reproducibility: manifest is missing"],
        )
    )
    gates.append(
        _gate(
            "license_manifest",
            []
            if package.licenses and package.licenses.entries
            else ["licenses: license manifest is missing or empty"],
        )
    )
    gates.append(
        _gate(
            "compile_check",
            []
            if package.compile_check and package.compile_check.passed
            else ["compile: package compile/check did not pass"],
        )
    )
    review = _review_state(package, fingerprint)
    gates.append(_gate("independent_review_and_dissent", list(review.findings)))

    all_findings = tuple(sorted(issue for gate in gates for issue in gate.findings))
    blocked = any(not gate.passed and gate.blocking for gate in gates)
    return ReadinessReport(
        label="READY FOR HUMAN REVIEW" if not blocked else "PACKAGING BLOCKED",
        status="PASS" if not blocked else "BLOCK",
        outcome=package.outcome.value,
        input_fingerprint=fingerprint,
        gates=tuple(gates),
        findings=all_findings,
    )
