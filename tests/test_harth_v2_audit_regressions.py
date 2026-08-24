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
from howhow.episodes.harth.v2 import run_guard as run_guard_module


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
    assert loaded.manifest["session_policy"] == "one_archive_member_per_subject_session"
    assert loaded.manifest["metrics_free"] is True
    assert "metrics" not in loaded.manifest
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
    bad["folds"][0]["states"]["calibrated"]["uncertainty"]["nll"] = [1.0, 1.0]  # type: ignore[index]
    with pytest.raises(ResultSchemaError):
        validate_result(bad)
    bad = copy.deepcopy(value)
    bad["folds"][0]["states"]["calibrated"]["class_support"]["rest"] = 1  # type: ignore[index]
    with pytest.raises(ResultSchemaError):
        validate_result(bad)
    bad["folds"][0]["states"]["calibrated"]["class_support"]["rest"] = 1  # type: ignore[index]
    with pytest.raises(ResultSchemaError):
        validate_result(bad)
    bad = copy.deepcopy(value)
    bad.pop("analysis")
    with pytest.raises(ResultSchemaError, match="analysis"):
        validate_result(bad)
    bad = copy.deepcopy(value)
    state = bad["folds"][0]["states"]["calibrated"]  # type: ignore[index]
    state["interval"] = state.pop("uncertainty")
    with pytest.raises(ResultSchemaError, match="named"):
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


@pytest.mark.parametrize("elapsed", [1800.0, 1800.0])
def test_f004_timeout_is_inclusive_at_fixed_deadline(
    tmp_path: Path, monkeypatch, elapsed: float
) -> None:
    clock = iter((100.0, 100.0 + elapsed))
    monkeypatch.setattr(run_guard_module.time, "monotonic", lambda: next(clock))
    guard = RunGuard(tmp_path, input_hash="a" * 64, protocol_hash="b" * 64)
    with pytest.raises(TimeoutError, match="1800"):
        guard.check_timeout()


def test_f001_duplicate_frozen_json_keys_fail_closed(tmp_path: Path) -> None:
    from scripts import harth_v2_run as runner

    path = tmp_path / "duplicate.json"
    path.write_text('{"protocol_id":"first","protocol_id":"second"}', encoding="utf-8")
    with pytest.raises(runner.PreflightFailure, match="duplicate JSON key"):
        runner.load_json(path)


def test_f002_complete_validator_approved_artifact_handoff() -> None:
    import importlib.util

    generator_path = Path("episodes/harth-calibration/paper/tools/generate_tables.py")
    spec = importlib.util.spec_from_file_location("harth_generator_test", generator_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.latex(_artifact())
    for marker in (
        "tab:results-folds",
        "tab:results-bootstrap",
        "tab:results-paired",
        "tab:results-comparisons",
        "tab:results-ablations",
        "tab:results-diagnostics",
        "NLL",
        "Brier",
        "ECE",
        "Support",
        "Preserved calibration failures",
    ):
        assert marker in text


def test_f003_restart_restores_exact_identity_and_completed_folds(tmp_path: Path) -> None:
    first = RunGuard(tmp_path, input_hash="a" * 64, protocol_hash="b" * 64, code_hash="c" * 64)
    first.record_fold("S001")
    first.checkpoint(phase="engine_fold")
    payload = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    resumed = RunGuard(tmp_path, input_hash="a" * 64, protocol_hash="b" * 64, code_hash="c" * 64)
    resumed.restore_checkpoint(payload)
    assert resumed.completed_folds == ["S001"]
    with pytest.raises(Exception, match="immutable"):
        RunGuard(
            tmp_path, input_hash="d" * 64, protocol_hash="b" * 64, code_hash="c" * 64
        ).restore_checkpoint(payload)


def _fake_loaded_archive():
    from howhow.episodes.harth.v2 import LoadedArchive

    windows = tuple(
        Window(f"S{index:03d}", f"harth/S{index:03d}.csv", "1", (0.0,) * 12, f"S{index}-{part}")
        for index in range(1, 23)
        for part in range(2)
    )
    subjects = [f"S{index:03d}" for index in range(1, 23)]
    return LoadedArchive(windows, {"subjects": subjects, "metrics_free": True}, len(windows))


@pytest.mark.parametrize("stage", ["loader", "resume", "engine", "schema", "generator", "final"])
def test_f003_command_boundary_failure_is_stage_guarded(
    tmp_path: Path, monkeypatch, stage: str
) -> None:
    from types import SimpleNamespace

    import howhow.episodes.harth.v2 as v2
    from scripts import harth_v2_run as runner

    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"private archive identity")
    output = tmp_path / stage
    args = SimpleNamespace(
        classes=["1", "2", "3", "4", "5", "6", "7", "8", "13", "14", "130", "140"],
        archive=archive,
        output=output,
        protocol=runner.DEFAULT_PROTOCOL,
        checkpoint=output / "checkpoint.json",
        resume=stage == "resume",
    )
    monkeypatch.setenv(runner.REAL_CONSENT_ENV, "1")
    source = _fake_loaded_archive()
    result = SimpleNamespace(status="COMPLETE", folds=[{} for _ in range(66)])
    monkeypatch.setattr(v2, "load_harth_archive", lambda *unused, **kwargs: source)
    monkeypatch.setattr(v2, "run_protocol", lambda *unused, **kwargs: result)
    if stage == "loader":
        monkeypatch.setattr(
            v2,
            "load_harth_archive",
            lambda *unused, **kwargs: (_ for _ in ()).throw(RuntimeError(stage)),
        )
    elif stage == "resume":
        output.mkdir()
        (output / "checkpoint.json").write_text(
            json.dumps({"input_hash": "wrong"}), encoding="utf-8"
        )
    elif stage == "engine":
        monkeypatch.setattr(
            v2, "run_protocol", lambda *unused, **kwargs: (_ for _ in ()).throw(RuntimeError(stage))
        )
    elif stage == "schema":
        monkeypatch.setattr(
            v2,
            "engine_result_to_schema",
            lambda *unused, **kwargs: (_ for _ in ()).throw(RuntimeError(stage)),
        )
    elif stage == "generator":
        monkeypatch.setattr(v2, "engine_result_to_schema", lambda *unused, **kwargs: _artifact())
        monkeypatch.setattr(v2, "validate_result", lambda value: value)
        monkeypatch.setattr(
            runner, "_validated_tables", lambda value: (_ for _ in ()).throw(RuntimeError(stage))
        )
    elif stage == "final":
        monkeypatch.setattr(v2, "engine_result_to_schema", lambda *unused, **kwargs: _artifact())
        monkeypatch.setattr(v2, "validate_result", lambda value: value)
        monkeypatch.setattr(runner, "_validated_tables", lambda value: "synthetic\\n")
        monkeypatch.setattr(
            "howhow.episodes.harth.v2.run_guard.RunGuard.final",
            lambda *unused, **kwargs: (_ for _ in ()).throw(RuntimeError(stage)),
        )
    if stage == "resume":
        with pytest.raises(runner.PreflightFailure, match="immutable"):
            runner.execute_real(args)
    else:
        with pytest.raises(RuntimeError, match=stage):
            runner.execute_real(args)
    failure = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "FAILED"
    assert failure["protocol_hash"] == runner.sha256_file(runner.DEFAULT_PROTOCOL)
    assert failure["code_hash"]
    expected_stage = {
        "loader": "loader_start",
        "resume": "resume_validation",
        "engine": "engine_start",
        "schema": "schema_start",
        "generator": "generator_start",
        "final": "generator_start",
    }[stage]
    assert (output / f"stage-{expected_stage}.json").is_file()
