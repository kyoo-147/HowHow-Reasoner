from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
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
    assert zero["status"] == "COMPLETE"
    assert all(
        zero["estimability"][metric]["status"] == "ESTIMABLE" for metric in ("nll", "brier", "ece")
    )
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


def test_real_auth_schema_rejects_unknown_fields_without_creating_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"not-a-zip")
    auth = tmp_path / "authorization.json"
    auth.write_text(json.dumps({"authorization_version": "v2.1", "unexpected": True}))
    output = tmp_path / "out"
    monkeypatch.setenv(_MODULE.REAL_CONSENT_ENV, "1")
    with pytest.raises(V21Error, match="AUTHORIZATION_SCHEMA_INVALID"):
        _MODULE.run_real(archive, auth, output)
    assert not output.exists()


def test_real_cli_auth_failure_has_no_destination_or_metric_stdout(tmp_path: Path) -> None:
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"not-a-zip")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({"authorization_version": "v2.1", "unexpected": True}))
    output = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "harth_v21_run.py"),
            "--execute-real",
            "--archive",
            str(archive),
            "--authorization",
            str(authorization),
            "--output",
            str(output),
        ],
        env={**os.environ, _MODULE.REAL_CONSENT_ENV: "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert not output.exists()


def test_real_generator_is_quarantined_and_unverified() -> None:
    generated = _MODULE.generate_outputs(
        {"status": "COMPLETE", "claim_boundary": "guarded_real_quarantined_no_release"}
    )
    payload = json.loads(generated["generator.json"])
    assert payload == {
        "claim_boundary": "guarded_real_quarantined_no_release",
        "performance_bearing": True,
        "real_data": True,
        "release": False,
        "scientific_status": "UNVERIFIED",
        "source_result_hash": payload["source_result_hash"],
        "status": "COMPLETE",
    }


def test_independent_decision_binds_exact_bytes_and_package_hashes(tmp_path: Path) -> None:
    auth = {
        "decision_id": "rerun-2026-08-24",
        "protocol_version": _MODULE.PROTOCOL_VERSION,
        "allow_rerun": True,
        "allow_resume": False,
        "allow_retry": False,
        "allow_tuning": False,
        "one_shot": True,
        "hashes": {"package": "a" * 64},
        "budgets": {"timeout_seconds": 1800, "bootstrap_reps": 2000, "pvalue_draws": 200000},
        "destination": str((tmp_path / "out").resolve()),
        "git_revision": "b" * 40,
        "vocabulary": _MODULE.CLASSES,
    }
    decision = tmp_path / "decision.json"
    decision.write_bytes(json.dumps(auth, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    auth["decision_sha256"] = __import__("hashlib").sha256(decision.read_bytes()).hexdigest()
    assert _MODULE._load_decision(decision, authorization=auth)["decision_id"] == "rerun-2026-08-24"
    decision.write_bytes(decision.read_bytes().replace(b"rerun-2026-08-24", b"tampered-2026-08-24"))
    with pytest.raises(V21Error, match="DECISION_HASH_MISMATCH"):
        _MODULE._load_decision(decision, authorization=auth)


def test_preexisting_destination_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "sentinel").write_text("keep")
    with pytest.raises(ValueError, match="FRESH_OUTPUT_REQUIRED"):
        run_synthetic("complete", output)
    assert (output / "sentinel").read_text() == "keep"


def test_real_cli_rejects_stale_authorization_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"not-a-zip")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(
        json.dumps(
            {
                "authorization_version": "v2.1",
                "protocol_version": _MODULE.PROTOCOL_VERSION,
                "allow_rerun": True,
                "one_shot": True,
                "hashes": {
                    key: "0" * 64
                    for key in ("protocol", "code", "config", "schema", "archive", "vocabulary")
                },
                "budgets": _MODULE.BUDGETS,
                "destination": str((tmp_path / "out").resolve()),
                "git_revision": "0" * 40,
                "vocabulary": _MODULE.CLASSES,
            }
        )
    )
    output = tmp_path / "out"
    monkeypatch.setenv(_MODULE.REAL_CONSENT_ENV, "1")
    monkeypatch.setattr(_MODULE, "_code_identity", lambda: ("1" * 64, "2" * 40))
    with pytest.raises(V21Error, match="STALE_OR_WRONG_AUTHORIZATION"):
        _MODULE.run_real(archive, authorization, output)
    assert not output.exists()
