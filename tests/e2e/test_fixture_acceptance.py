import json
import subprocess
import sys
from pathlib import Path


def test_fixture_mechanics_acceptance_runs_to_human_review() -> None:
    root = Path(__file__).parents[2]
    output = subprocess.check_output(
        [sys.executable, "scripts/e2e_fixture.py"], cwd=root, text=True
    )
    result = json.loads(output)
    assert result["label"] == "FIXTURE"
    assert result["recovery_idempotent"]
    assert all(run["final_state"] == "READY FOR HUMAN REVIEW" for run in result["runs"])
    assert all(run["scientific_evidence"].startswith("UNVERIFIED") for run in result["runs"])
    assert all(run["failed_run_preserved"] for run in result["runs"])
    assert all(run["approval_denial"] for run in result["runs"])
    assert all(run["checksum_corruption_rejected"] for run in result["runs"])
