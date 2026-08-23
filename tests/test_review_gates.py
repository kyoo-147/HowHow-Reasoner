import pytest

from howhow.gates import (
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
    evaluate_readiness,
    package_fingerprint,
)
from howhow.review import FindingSeverity, ReviewerRecord, ReviewFinding, ReviewStatus


def complete_package() -> PackageInput:
    source = "The measured effect was 0.5 in the held-out run."
    package = PackageInput(
        claims=(
            ClaimEvidence(
                claim_id="claim-1",
                wording="The measured effect was 0.5.",
                citations=(
                    CitationSpan(
                        evidence_id="e-1",
                        source_id="run-1",
                        locator="results.txt:1",
                        quote=source,
                        start=0,
                        end=len(source),
                        source_text=source,
                    ),
                ),
            ),
        ),
        lineage=(ArtifactLineage(artifact_id="artifact-1", run_id="run-1"),),
        runs=(RunRecord(run_id="run-1", status="SUCCEEDED", artifact_ids=("artifact-1",)),),
        statistical_plan=StatisticalPlan(content="pre-registered two-sided test"),
        reproducibility=ReproducibilityManifest(code_revision="abc", command=("uv", "run")),
        licenses=LicenseManifest(entries=("MIT: project",)),
        compile_check=CompileCheck(passed=True, command="uv run pytest"),
        outcome=Outcome.POSITIVE,
    )
    fingerprint = package_fingerprint(package)
    return package.model_copy(
        update={
            "reviewer_records": (
                ReviewerRecord(
                    review_id="review-1",
                    reviewer_id="reviewer-a",
                    input_fingerprint=fingerprint,
                    status=ReviewStatus.ACCEPTED,
                ),
            )
        }
    )


def test_complete_package_is_ready_without_claiming_truth() -> None:
    report = evaluate_readiness(complete_package())
    assert report.ready
    assert report.label == "READY FOR HUMAN REVIEW"
    assert report.scientific_truth == "NOT_EVALUATED"


def test_missing_and_result_only_evidence_block() -> None:
    package = complete_package().model_copy(
        update={"claims": (ClaimEvidence(claim_id="result", wording="It won", result_only=True),)}
    )
    report = evaluate_readiness(package)
    assert not report.ready
    assert any("result-only" in finding for finding in report.findings)
    assert any("result-only" in finding for finding in report.findings)


def test_fabricated_looking_citation_is_rejected() -> None:
    with pytest.raises(ValueError, match="exact source span"):
        CitationSpan(
            evidence_id="fake",
            source_id="source",
            locator="page:1",
            quote="fabricated quote",
            start=0,
            end=16,
            source_text="different source",
        )


def test_failed_compile_blocks() -> None:
    package = complete_package().model_copy(
        update={"compile_check": CompileCheck(passed=False, command="uv run pytest")}
    )
    assert not evaluate_readiness(package).ready


def test_unresolved_dissent_blocks_until_immutable_response() -> None:
    package = complete_package()
    fingerprint = package_fingerprint(package)
    finding = ReviewFinding(
        finding_id="dissent-1",
        severity=FindingSeverity.BLOCKING,
        message="Needs evidence",
        dissent=True,
    )
    review = ReviewerRecord(
        review_id="review-2",
        reviewer_id="reviewer-b",
        input_fingerprint=fingerprint,
        status=ReviewStatus.REVISE,
        findings=(finding,),
    )
    blocked = package.model_copy(update={"reviewer_records": package.reviewer_records + (review,)})
    assert not evaluate_readiness(blocked).ready


def test_failed_runs_and_empty_license_manifest_block() -> None:
    package = complete_package().model_copy(
        update={
            "runs": (RunRecord(run_id="run-1", status="FAILED", artifact_ids=()),),
            "licenses": LicenseManifest(),
        }
    )
    report = evaluate_readiness(package)
    assert not report.ready
    assert any("failed run" in finding for finding in report.findings)
    assert any("license" in finding for finding in report.findings)


def test_stale_review_and_unresolved_dissent_block() -> None:
    base = complete_package()
    changed = base.model_copy(update={"statistical_plan": StatisticalPlan(content="changed")})
    report = evaluate_readiness(changed)
    assert not report.ready
    assert any("stale review" in finding for finding in report.findings)


def test_negative_and_inconclusive_are_explicit_outcomes() -> None:
    for outcome in (Outcome.NEGATIVE, Outcome.INCONCLUSIVE, Outcome.BLOCKED):
        package = complete_package().model_copy(update={"outcome": outcome})
        assert evaluate_readiness(package).outcome == outcome.value
