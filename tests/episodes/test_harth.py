from __future__ import annotations

import numpy as np
import pytest

from howhow.episodes.harth import (
    assert_no_subject_leakage,
    bootstrap_confidence_interval,
    calibration_metrics,
    subject_held_out_split,
)
from howhow.episodes.harth.baseline import NearestCentroidBaseline


def test_synthetic_fixture_is_pipeline_only_and_metrics_are_bounded() -> None:
    x = np.array([[0.0, 0.1], [0.1, 0.0], [3.0, 3.1], [3.1, 3.0]])
    y = np.array([0, 0, 1, 1])
    model = NearestCentroidBaseline().fit(x[:2], y[:2])
    # Pipeline fixture uses a complete class set for a valid probability check.
    model = NearestCentroidBaseline().fit(x, y)
    metrics = calibration_metrics(model.predict_proba(x), y, bins=5)
    assert set(metrics) == {"nll", "brier", "ece", "ece_bins", "n"}
    assert 0 <= metrics["brier"] <= 2
    assert 0 <= metrics["ece"] <= 1


def test_subject_split_and_leakage_guard() -> None:
    split = subject_held_out_split(["S01", "S02", "S03"], ["S03"], seed=0)
    assert split.frozen and split.train_subjects == ("S01", "S02")
    assert_no_subject_leakage(split.train_subjects, split.test_subjects)
    with pytest.raises(ValueError, match="leakage"):
        assert_no_subject_leakage(["S01"], ["S01"])


def test_bootstrap_is_deterministic() -> None:
    values = [0.1, 0.2, 0.3, 0.4]
    assert bootstrap_confidence_interval(values, reps=200, seed=7) == bootstrap_confidence_interval(
        values, reps=200, seed=7
    )
