from __future__ import annotations

import json

import numpy as np
import pytest

from howhow.episodes.harth.v2 import (
    ProtocolFailure,
    Window,
    fit_temperature,
    holm_correction,
    nested_loso_folds,
    paired_subject_bootstrap,
    run_protocol,
)


def fixture_windows() -> list[Window]:
    rows: list[Window] = []
    for subject, offset in (("S01", 0.0), ("S02", 4.0), ("S03", 8.0)):
        for index in range(6):
            label = "rest" if index < 3 else "walk"
            value = offset + (0.0 if label == "rest" else 2.0)
            rows.append(
                Window(subject, "session-a", label, tuple([value] * 12), f"{subject}-{index}")
            )
    return rows


def test_no_test_derived_fitting_and_adversarial_leakage() -> None:
    windows = fixture_windows()
    baseline = run_protocol(windows, ["rest", "walk"])
    contaminated = run_protocol(
        [
            window
            if window.subject != "S03"
            else Window(
                window.subject,
                window.session,
                window.label,
                tuple([10_000.0] * 12),
                window.provenance,
            )
            for window in windows
        ],
        ["rest", "walk"],
    )
    first = next(row for row in baseline.folds if row["test_subject"] == "S03")
    changed = next(row for row in contaminated.folds if row["test_subject"] == "S03")
    assert first["selected_temperature"] == changed["selected_temperature"]
    assert first["train_subjects"] == ["S01", "S02"]
    with pytest.raises(ProtocolFailure, match="duplicate provenance"):
        run_protocol(windows + [windows[0]], ["rest", "walk"])
    with pytest.raises(ProtocolFailure, match="outside"):
        run_protocol(
            windows + [Window("S01", "a", "unknown", (0.0,) * 12, "new")], ["rest", "walk"]
        )


def test_missing_class_is_a_hard_failure() -> None:
    windows = [w for w in fixture_windows() if not (w.subject == "S03" and w.label == "walk")]
    with pytest.raises(ProtocolFailure, match="class coverage"):
        run_protocol(windows, ["rest", "walk"])


def test_timeout_and_deterministic_checkpoint_restart(tmp_path) -> None:
    with pytest.raises(ProtocolFailure, match="timeout"):
        run_protocol(fixture_windows(), ["rest", "walk"], timeout_seconds=0)
    checkpoint = tmp_path / "checkpoint.json"
    first = run_protocol(fixture_windows(), ["rest", "walk"], checkpoint=checkpoint)
    restarted = run_protocol(fixture_windows(), ["rest", "walk"], checkpoint=checkpoint)
    assert first.to_dict() == restarted.to_dict()
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["input_hash"] == first.input_hash
    with pytest.raises(ProtocolFailure, match="immutable hash"):
        run_protocol(
            fixture_windows() + [Window("S04", "a", "rest", (1.0,) * 12, "S04-0")],
            ["rest", "walk"],
            checkpoint=checkpoint,
        )


def test_optimizer_is_bounded_deterministic_and_tie_breaks() -> None:
    logits = np.array([[4.0, 0.0], [0.0, 4.0], [3.0, 1.0], [1.0, 3.0]])
    labels = np.array([0, 1, 0, 1])
    first = fit_temperature(logits, labels)
    assert 0.05 <= first[0] <= 20.0
    assert first == fit_temperature(logits, labels)
    assert fit_temperature(np.zeros((4, 2)), labels)[0] == pytest.approx(0.05)
    with pytest.raises(ProtocolFailure):
        fit_temperature(logits, labels, bounds=(0.0, 20.0))


def test_ablation_channels_and_fold_determinism() -> None:
    windows = fixture_windows()
    first = nested_loso_folds(windows)
    second = nested_loso_folds(list(reversed(windows)))
    assert [(fold.test_subject, fold.train_subjects) for fold in first] == [
        (fold.test_subject, fold.train_subjects) for fold in second
    ]
    result = run_protocol(windows, ["rest", "walk"])
    assert {row["configuration"] for row in result.folds} == {
        "full_sensor",
        "back_only",
        "thigh_only",
    }
    assert len(result.folds) == 9
    assert all(row["calibration_state"] == ["uncalibrated", "calibrated"] for row in result.folds)


def test_paired_cluster_bootstrap_and_holm_are_deterministic() -> None:
    differences = {"S01": -0.2, "S02": 0.1, "S03": -0.05}
    first = paired_subject_bootstrap(differences)
    assert first == paired_subject_bootstrap(differences)
    assert first["repetitions"] == 2000
    with pytest.raises(ProtocolFailure, match="2000"):
        paired_subject_bootstrap(differences, reps=100)
    correction = holm_correction({"nll": 0.01, "brier": 0.03, "ece": 0.2})
    assert correction["adjusted_p"] == {"nll": 0.03, "brier": 0.06, "ece": 0.2}
    assert set(correction["reject"]) == {"nll", "brier", "ece"}


def test_result_contains_paired_comparisons_and_immutable_hashes() -> None:
    result = run_protocol(fixture_windows(), ["rest", "walk"])
    assert result.status == "COMPLETE"
    assert len(result.input_hash) == len(result.protocol_hash) == 64
    assert set(result.comparisons) >= {"full_sensor", "back_only", "thigh_only", "holm_primary"}
    assert result.comparisons["full_sensor"]["nll"]["bootstrap"]["repetitions"] == 2000
