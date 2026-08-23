#!/usr/bin/env python3
"""Deterministic FIXTURE-only mechanics acceptance for HowHow.

This deliberately proves control-plane and packaging mechanics, not scientific truth.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from howhow.api import create_app
from howhow.contracts import ID, TaskSpec
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
from howhow.ledger import EventStore
from howhow.publication import BuildConfig, PackageBuilder, PackageValidationError
from howhow.review import (
    FindingSeverity,
    ReviewAction,
    ReviewerRecord,
    ReviewFinding,
    ReviewResponse,
    ReviewStatus,
)
from howhow.runners import LocalSubprocessProvider, RunnerRequest
from howhow.workflows import EpisodeWorkflow

FIXTURE = "FIXTURE"


def _run_cli(*args: str) -> str:
    return subprocess.check_output([sys.executable, "-m", "howhow.cli", *args], text=True).strip()


def _fake_tex(root: Path) -> tuple[Path, Path]:
    script = root / "fixture_tex.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "Path('main.pdf').write_bytes(b'%PDF-FIXTURE')\n",
        encoding="utf-8",
    )
    bib = root / "fixture_bib.py"
    bib.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "Path('fixture-bib-ran').write_text('FIXTURE')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        # Python itself is the executable; the scripts are passed as explicit argv.
        return Path(sys.executable), Path(sys.executable)
    script.chmod(0o755)
    bib.chmod(0o755)
    return script, bib


def _append_mechanics_events(workflow: EpisodeWorkflow) -> None:
    workflow._append(
        "evidence.recorded",
        "evidence",
        "FIXTURE:evidence",
        {"label": FIXTURE, "status": "UNVERIFIED"},
    )
    workflow._append(
        "baseline.recorded", "run", "FIXTURE:baseline", {"label": FIXTURE, "status": "SUCCEEDED"}
    )
    workflow._append("result.recorded", "run", "FIXTURE:result", {"label": FIXTURE, "metric": 0.5})


def _package_input() -> PackageInput:
    source = "FIXTURE measured effect 0.5 in bounded run."
    base = PackageInput(
        claims=(
            ClaimEvidence(
                claim_id="FIXTURE:claim",
                wording=source,
                citations=(
                    CitationSpan(
                        evidence_id="FIXTURE:evidence",
                        source_id="FIXTURE:source",
                        locator="FIXTURE:results:1",
                        quote=source,
                        start=0,
                        end=len(source),
                        source_text=source,
                    ),
                ),
            ),
        ),
        lineage=(ArtifactLineage(artifact_id="FIXTURE:artifact", run_id="FIXTURE:success"),),
        runs=(
            RunRecord(
                run_id="FIXTURE:success", status="SUCCEEDED", artifact_ids=("FIXTURE:artifact",)
            ),
            RunRecord(run_id="FIXTURE:failed", status="FAILED", failure_preserved=True),
        ),
        statistical_plan=StatisticalPlan(content="FIXTURE locked metric plan"),
        reproducibility=ReproducibilityManifest(
            code_revision="FIXTURE:revision", command=("FIXTURE", "run"), seed=7
        ),
        licenses=LicenseManifest(entries=("FIXTURE synthetic material",)),
        compile_check=CompileCheck(passed=True, command="FIXTURE compile"),
        outcome=Outcome.INCONCLUSIVE,
    )
    fingerprint = package_fingerprint(base)
    dissent = ReviewerRecord(
        review_id="FIXTURE:dissent-review",
        reviewer_id="FIXTURE:reviewer-b",
        input_fingerprint=fingerprint,
        status=ReviewStatus.REVISE,
        findings=(
            ReviewFinding(
                finding_id="FIXTURE:dissent",
                severity=FindingSeverity.BLOCKING,
                message="FIXTURE bounded-run limitation requires acknowledgement",
                dissent=True,
            ),
        ),
    )
    response = ReviewResponse(
        response_id="FIXTURE:dissent-resolution",
        finding_id="FIXTURE:dissent",
        reviewer_id="FIXTURE:reviewer-a",
        action=ReviewAction.RESOLVE,
        reason="FIXTURE limitation retained and resolved for package readiness",
        input_fingerprint=fingerprint,
    )
    return base.model_copy(update={"reviewer_records": (dissent,), "responses": (response,)})


def acceptance(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    cli_project = root / "cli-project"
    _run_cli("init", str(cli_project), "--project-id", "FIXTURE:cli")
    project = root / "project"
    api_root = root / "api"
    api_root.mkdir(parents=True, exist_ok=True)
    client = TestClient(create_app(project_root=api_root))
    created = client.post(
        "/projects", json={"project_id": "FIXTURE-api", "name": "FIXTURE API project"}
    )
    assert created.status_code == 200
    idem = {"Idempotency-Key": "FIXTURE:brief-idempotency"}
    brief = {
        "question": "FIXTURE question",
        "scope": ["FIXTURE scope"],
        "constraints": ["FIXTURE bounded"],
    }
    first = client.post("/projects/FIXTURE-api/briefs", json=brief, headers=idem)
    second = client.post("/projects/FIXTURE-api/briefs", json=brief, headers=idem)
    assert first.status_code == second.status_code == 200
    assert first.json()["event"]["event_id"] == second.json()["event"]["event_id"]
    denied = client.post(
        "/projects/FIXTURE-api/tasks",
        json={
            "task_id": "FIXTURE:denied",
            "objective": "FIXTURE denied task",
            "provider_id": "FIXTURE:provider",
            "idempotency_key": "FIXTURE:denied-v1",
        },
    )
    assert denied.status_code == 403
    approval = client.post(
        "/projects/FIXTURE-api/approvals",
        json={"scope": "FIXTURE run", "actor_id": "FIXTURE-human"},
    )
    assert approval.status_code == 200
    approval_id = approval.json()["approval"]["approval_id"]["value"]
    task = client.post(
        "/projects/FIXTURE-api/tasks",
        json={
            "task_id": "FIXTURE:task",
            "objective": "FIXTURE bounded task",
            "provider_id": "FIXTURE:provider",
            "idempotency_key": "FIXTURE:task-v1",
            "approval_id": approval_id,
        },
    )
    assert task.status_code == 200
    assert client.get("/projects/FIXTURE-api/status").json()["event_count"] == 3

    workflow = EpisodeWorkflow.create(
        project, project_id="FIXTURE:episode", question="FIXTURE deterministic episode"
    )
    workflow.approve()
    workflow.plan()
    _append_mechanics_events(workflow)
    provider = LocalSubprocessProvider()
    spec = TaskSpec(
        task_id=ID(value="FIXTURE:runner-task"),
        project_id=ID(value="FIXTURE:episode"),
        objective="FIXTURE runner",
        provider=provider.identity,
        idempotency_key="FIXTURE:runner-v1",
        occurred_at=datetime.now(UTC),
    )
    response = provider.run(
        RunnerRequest(
            task=spec,
            argv=(sys.executable, "-c", "print('FIXTURE runner')"),
            cwd=str(root),
            env={},
            timeout_seconds=5.0,
            code_revision="FIXTURE:revision",
            seed=7,
        )
    )
    failed = provider.run(
        RunnerRequest(
            task=spec,
            argv=(sys.executable, "-c", "raise SystemExit(3)"),
            cwd=str(root),
            env={},
            timeout_seconds=5.0,
            code_revision="FIXTURE:revision",
            seed=7,
        )
    )
    assert response.state.value == "completed" and failed.state.value == "failed"
    for kind in ("literature", "baseline", "review"):
        workflow.complete_task(kind)
    package_input = _package_input()
    report = evaluate_readiness(package_input)
    assert report.ready and any("dissent" in gate.name for gate in report.gates)
    source = Path("templates/paper")
    latex, bib = _fake_tex(root)
    # On Windows use explicit Python argv-compatible wrappers through a temporary .cmd.
    if os.name == "nt":
        latex_cmd = root / "fixture-latex.cmd"
        bib_cmd = root / "fixture-bib.cmd"
        latex_cmd.write_text(
            f'@echo off\n"{sys.executable}" "{root / "fixture_tex.py"}" %*\n', encoding="utf-8"
        )
        bib_cmd.write_text(
            f'@echo off\n"{sys.executable}" "{root / "fixture_bib.py"}" %*\n', encoding="utf-8"
        )
        latex, bib = latex_cmd, bib_cmd
    dist = root / "dist"
    built = PackageBuilder(BuildConfig(latex=latex, bibtex=bib, timeout_seconds=10)).build(
        source, dist, evidence_reviewed=True, human_reviewed=True, reproducible=True
    )
    assert built.ready and PackageBuilder.check(dist)["checksums"]
    assert built.ready and PackageBuilder.check(dist)["checksums"]
    pdf = dist / "paper.pdf"
    original_pdf = pdf.read_bytes()
    pdf.write_bytes(original_pdf + b"FIXTURE-corruption")
    try:
        PackageBuilder.check(dist)
    except PackageValidationError:
        pass
    else:
        raise AssertionError("FIXTURE checksum corruption was not rejected")
    pdf.write_bytes(original_pdf)
    assert PackageBuilder.check(dist)["checksums"]
    restarted = EpisodeWorkflow(project)
    assert restarted.snapshot.state.value == "PACKAGING"
    EventStore(project).verify()
    return {
        "label": FIXTURE,
        "scientific_evidence": "UNVERIFIED: fixture mechanics only",
        "final_state": "READY FOR HUMAN REVIEW",
        "package_label": json.loads((dist / "package-manifest.json").read_text())["label"],
        "package_checksums": True,
        "source_zip": (dist / "arxiv-source.zip").is_file(),
        "failed_run_preserved": True,
        "runner_success": response.task_result.status.value,
        "runner_failure": failed.task_result.status.value,
        "event_chain_verified": True,
        "api_idempotent": True,
        "approval_denial": True,
        "checksum_corruption_rejected": True,
        "review_dissent_resolved": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="howhow-fixture-") as directory:
        first = acceptance(Path(directory) / "first")
        second = acceptance(Path(directory) / "second")
    result = {"label": FIXTURE, "runs": [first, second], "recovery_idempotent": first == second}
    if not result["recovery_idempotent"]:
        raise SystemExit("FIXTURE acceptance was not deterministic")
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        if args.output.exists():
            raise SystemExit("refusing to overwrite immutable acceptance report")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
