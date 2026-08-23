from __future__ import annotations

import copy

import pytest

from howhow.episodes.harth.v2 import ProtocolFailure, Window, run_protocol, summarize_fold_rows


def rows() -> list[dict[str, object]]:
    windows: list[Window] = []
    for subject, offset in (("S01", 0.0), ("S02", 4.0), ("S03", 8.0)):
        for index in range(6):
            label = "rest" if index < 3 else "walk"
            value = offset + (0.0 if label == "rest" else 2.0)
            windows.append(Window(subject, "session-a", label, (value,) * 12, f"{subject}-{index}"))
    return run_protocol(windows, ["rest", "walk"]).folds


def test_complete_macro_cluster_and_comparison_outputs_are_deterministic() -> None:
    first = summarize_fold_rows(rows())
    second = summarize_fold_rows(rows())
    assert first == second
    assert first["claim_boundary"] == "synthetic_or_supplied_only"
    assert (
        first["configurations"]["full_sensor"]["states"]["calibrated"]["macro_f1"]["estimate"] >= 0
    )
    assert (
        first["configurations"]["full_sensor"]["states"]["calibrated"]["nll"]["repetitions"] == 2000
    )
    assert set(first["primary_calibration"]["raw_p"]) == {"nll", "brier", "ece"}
    assert first["exploratory_ablation"]["raw_p"]


def test_nonfinite_and_mismatched_populations_fail_closed() -> None:
    bad = copy.deepcopy(rows())
    bad[0]["calibrated"]["S01"]["nll"] = float("nan")  # type: ignore[index]
    with pytest.raises(ProtocolFailure, match="nonfinite"):
        summarize_fold_rows(bad)

    bad = copy.deepcopy(rows())
    del bad[0]["calibrated"]["S01"]  # type: ignore[index]
    with pytest.raises(ProtocolFailure, match="missing"):
        summarize_fold_rows(bad)

    bad = copy.deepcopy(rows())
    bad[-1]["test_subject"] = "S04"
    with pytest.raises(ProtocolFailure, match="mismatched|missing"):
        summarize_fold_rows(bad)
