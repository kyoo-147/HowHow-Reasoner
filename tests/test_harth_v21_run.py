from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "harth_v21_run", Path(__file__).parents[1] / "scripts" / "harth_v21_run.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
run_synthetic = _MODULE.run_synthetic
V21Error = _MODULE.V21Error


def test_complete_fixture_publishes_quarantined_truthful_artifacts(tmp_path: Path) -> None:
    result = run_synthetic("complete", tmp_path / "out")
    payload = json.loads(result.read_text())
    assert payload["status"] == "COMPLETE"
    assert payload["claim_boundary"] == "synthetic_structural_only_no_performance_claim"
    assert set(payload["hashes"]) == {
        "protocol_sha256",
        "schema_sha256",
        "config_sha256",
        "code_sha256",
        "input_sha256",
        "vocabulary_sha256",
        "eligibility_manifest_sha256",
        "pairing_manifest_sha256",
    }
    assert json.loads((result.parent / "quarantine.json").read_text())["real_data"] is False
    assert "No performance claim" in (result.parent / "manuscript.md").read_text()


def test_support_and_family_failures_are_preserved(tmp_path: Path) -> None:
    zero = json.loads(run_synthetic("zero-support", tmp_path / "zero").read_text())
    incomplete = json.loads(run_synthetic("incomplete-family", tmp_path / "family").read_text())
    assert zero["status"] == "NOT_ESTIMABLE"
    assert "140" in zero["support"]["held_out_test"]["zero_support"]
    assert incomplete["status"] == "INCOMPLETE_FAMILY"


@pytest.mark.parametrize("fixture", ["schema-failure", "timeout", "dirty-identity"])
def test_stage_failures_are_atomic_and_metrics_free(tmp_path: Path, fixture: str) -> None:
    output = tmp_path / fixture
    with pytest.raises((V21Error, TimeoutError)):
        run_synthetic(fixture, output)
    failure = json.loads((output / "failure.json").read_text())
    assert failure["scientific_metrics"] is False
    assert failure["phase"] == "loader_engine_analysis_schema_generator"
    assert not (output / "result-v2.1.json").exists()


def test_preexisting_destination_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "sentinel").write_text("keep")
    with pytest.raises(ValueError, match="FRESH_OUTPUT_REQUIRED"):
        run_synthetic("complete", output)
    assert (output / "sentinel").read_text() == "keep"
