"""Regression coverage for the independent HARTH v2 readiness findings."""

from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

import pytest

from howhow.episodes.harth.v2 import (
    ResultSchemaError,
    RunGuard,
    Window,
    engine_result_to_schema,
    load_harth_archive,
    run_protocol,
    validate_result,
)


def _archive(path: Path) -> None:
    header = "timestamp,label,back_x,back_y,back_z,thigh_x,thigh_y,thigh_z\n"
    rows = "".join(
        f"2025-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00,"
        f"{('rest' if i < 64 else 'walk')},1,1,1,2,2,2\n"
        for i in range(128)
    )
    with zipfile.ZipFile(path, "w") as bundle:
        for subject in ("S001", "S002"):
            bundle.writestr(f"harth/{subject}.csv", header + rows)


def test_f001_canonical_member_is_the_session_boundary(tmp_path: Path) -> None:
    archive = tmp_path / "harth.zip"
    _archive(archive)
    loaded = load_harth_archive(archive, ["rest", "walk"], window_size=32, stride=32)
    assert loaded.manifest["session_policy"] == "one_archive_member_per_subject_session"
    assert loaded.manifest["session_boundaries"] == ["harth/S001.csv", "harth/S002.csv"]
    assert {window.session for window in loaded.windows} == {
        "harth/S001.csv",
        "harth/S002.csv",
    }


def _artifact() -> dict[str, object]:
    windows = [
        Window(subject, "s", "rest" if i < 3 else "walk", (float(i),) * 12, f"{subject}-{i}")
        for subject in ("S01", "S02", "S03")
        for i in range(6)
    ]
    return engine_result_to_schema(run_protocol(windows, ["rest", "walk"]), code_hash="a" * 64)


def test_f002_schema_rejects_degenerate_uncertainty_and_fake_support() -> None:
    value = _artifact()
    bad = copy.deepcopy(value)
    bad["folds"][0]["states"]["calibrated"]["interval"] = [1.0, 1.0]  # type: ignore[index]
    with pytest.raises(ResultSchemaError):
        validate_result(bad)
    bad = copy.deepcopy(value)
    bad["folds"][0]["states"]["calibrated"]["class_support"]["rest"] = 1  # type: ignore[index]
    with pytest.raises(ResultSchemaError):
        validate_result(bad)


def test_f003_analysis_contains_uncertainty_ablation_and_failure_channels() -> None:
    value = _artifact()
    analysis = value["analysis"]
    assert set(analysis["configurations"]) == {"full_sensor", "back_only", "thigh_only"}
    assert analysis["primary_calibration"]["adjusted_p"]
    assert analysis["exploratory_ablation"]["adjusted_p"]
    assert "failures" in analysis["diagnostics"]


def test_f004_guard_preserves_failure_and_exact_resume_identity(tmp_path: Path) -> None:
    guard = RunGuard(tmp_path, input_hash="a" * 64, protocol_hash="b" * 64, code_hash="c" * 64)
    guard.checkpoint(phase="fold", hashes={"input_hash": "a" * 64})
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["scientific_metrics"] is False
    assert checkpoint["input_hash"] == "a" * 64
    with pytest.raises(RuntimeError):
        guard.execute(lambda: (_ for _ in ()).throw(RuntimeError("interrupted")))
    failure = json.loads((tmp_path / "failure.json").read_text())
    assert failure["status"] == "FAILED"
    assert failure["scientific_metrics"] is False
