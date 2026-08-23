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


def test_safe_extract_rejects_traversal(tmp_path) -> None:
    import zipfile

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.csv", "bad")
    from howhow.episodes.harth.smoke import safe_extract_zip

    with pytest.raises(ValueError, match="unsafe"):
        safe_extract_zip(archive, tmp_path / "out")


def test_bounded_harth_window_loader(tmp_path) -> None:
    from howhow.episodes.harth.smoke import load_windows

    csv_path = tmp_path / "subject01.csv"
    header = "timestamp,back_x,back_y,back_z,thigh_x,thigh_y,thigh_z,label,subject\n"
    rows = "".join(f"{index},1,2,3,4,5,6,walking,S001\n" for index in range(8))
    csv_path.write_text(header + rows, encoding="utf-8")
    features, labels, subjects, details = load_windows(
        [csv_path], max_rows=8, max_subjects=1, window_size=4, stride=2
    )
    assert features.shape == (3, 12)
    assert labels.tolist() == ["walking"] * 3
    assert subjects == ["S001"] * 3
    assert details["rows_read"] == 8


def test_bounded_harth_window_loader_uses_filename_subject_fallback(tmp_path) -> None:
    from howhow.episodes.harth.smoke import load_windows

    csv_path = tmp_path / "S006.csv"
    header = "timestamp,back_x,back_y,back_z,thigh_x,thigh_y,thigh_z,label\n"
    rows = "".join(f"{index},1,2,3,4,5,6,walking\n" for index in range(4))
    csv_path.write_text(header + rows, encoding="utf-8")
    _, _, subjects, _ = load_windows(
        [csv_path], max_rows=4, max_subjects=1, window_size=4, stride=1
    )
    assert subjects == ["S006"]


def test_bounded_harth_window_loader_requires_subject_when_filename_has_none(tmp_path) -> None:
    from howhow.episodes.harth.smoke import load_windows

    csv_path = tmp_path / "activities.csv"
    header = "timestamp,back_x,back_y,back_z,thigh_x,thigh_y,thigh_z,label\n"
    csv_path.write_text(header, encoding="utf-8")
    with pytest.raises(ValueError, match="subject"):
        load_windows([csv_path], max_rows=4, max_subjects=1, window_size=4, stride=1)


def test_harth_cli_exits_nonzero_for_failed_manifest(tmp_path, monkeypatch) -> None:
    import json
    import sys

    from howhow.episodes.harth import smoke

    manifest = tmp_path / "failed.json"
    manifest.write_text(json.dumps({"status": "FAILED"}), encoding="utf-8")
    monkeypatch.setenv("HOWHOW_RUN_REAL_HARTH", "1")
    monkeypatch.setattr(smoke, "run_smoke", lambda args: manifest)
    monkeypatch.setattr(sys, "argv", ["harth-smoke"])
    with pytest.raises(SystemExit) as raised:
        smoke.main()
    assert raised.value.code == 1


def test_harth_download_rejects_expired_deadline_before_open(tmp_path) -> None:
    import time

    from howhow.episodes.harth.download import download_harth

    opened = False

    def opener(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("expired downloads must not open a connection")

    with pytest.raises(TimeoutError, match="deadline expired"):
        download_harth(tmp_path / "harth.zip", deadline=time.monotonic() - 1, opener=opener)
    assert not opened


def test_harth_download_bounds_stalled_read_and_preserves_partial(tmp_path) -> None:
    import time

    from howhow.episodes.harth.download import download_harth

    class StalledResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            if size != 1024 * 1024:
                raise AssertionError("download must use bounded chunks")
            if not hasattr(self, "sent"):
                self.sent = True
                return b"partial"
            raise TimeoutError("simulated stalled socket read")

    timeouts = []

    def opener(request, *, timeout):
        timeouts.append(timeout)
        return StalledResponse()

    destination = tmp_path / "harth.zip"
    with pytest.raises(TimeoutError, match="stalled"):
        download_harth(destination, deadline=time.monotonic() + 30, opener=opener)
    assert 0 < timeouts[0] <= 30
    assert not destination.exists()
    assert (tmp_path / "harth.zip.part").read_bytes() == b"partial"
