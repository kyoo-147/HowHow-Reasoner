# ruff: noqa: E501
from __future__ import annotations

import csv
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from howhow.episodes.harth.v2 import LoaderFailure, RunGuard, load_harth_archive

HEADER = [
    "timestamp",
    "subject",
    "session",
    "label",
    "back_x",
    "back_y",
    "back_z",
    "thigh_x",
    "thigh_y",
    "thigh_z",
]


def make_csv(
    subject: str, *, session: str = "a", count: int = 128, bad_at: int | None = None
) -> str:
    lines: list[list[object]] = [HEADER]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for index in range(count):
        timestamp = start + timedelta(seconds=index)
        if bad_at == index:
            timestamp -= timedelta(seconds=2)
        lines.append([timestamp.isoformat(), subject, session, "walk", 1, 2, 3, 4, 5, 6])
    output: list[str] = []
    for row in lines:
        from io import StringIO

        stream = StringIO()
        csv.writer(stream).writerow(row)
        output.append(stream.getvalue().rstrip("\r\n"))
    return "\n".join(output) + "\n"


def archive(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in members.items():
            bundle.writestr(name, content)
    return path


def test_loader_is_canonical_and_emits_no_metrics(tmp_path: Path) -> None:
    first = archive(
        tmp_path / "a.zip", {"S002_a.csv": make_csv("S002"), "S001_a.csv": make_csv("S001")}
    )
    second = archive(
        tmp_path / "b.zip", {"S001_a.csv": make_csv("S001"), "S002_a.csv": make_csv("S002")}
    )
    left = load_harth_archive(first, ["walk"])
    right = load_harth_archive(second, ["walk"])
    assert left.manifest["scientific_metrics"] is False
    assert [window.provenance for window in left.windows] == [
        window.provenance for window in right.windows
    ]
    assert left.manifest["files"] == right.manifest["files"]


@pytest.mark.parametrize("member", ["../escape.csv", "/absolute.csv"])
def test_unsafe_zip_members_fail_closed(tmp_path: Path, member: str) -> None:
    with pytest.raises(LoaderFailure, match="unsafe"):
        load_harth_archive(archive(tmp_path / "unsafe.zip", {member: make_csv("S001")}), ["walk"])


def test_nonmonotonic_and_ambiguous_identity_fail(tmp_path: Path) -> None:
    with pytest.raises(LoaderFailure, match="nonmonotonic"):
        load_harth_archive(
            archive(
                tmp_path / "bad.zip",
                {"S001_a.csv": make_csv("S001", bad_at=4), "S002_a.csv": make_csv("S002")},
            ),
            ["walk"],
        )
    content = make_csv("S001").replace("S001,a", "S001,a", 1).replace("S001,a", "S002,a", 1)
    with pytest.raises(LoaderFailure, match="ambiguous subject"):
        load_harth_archive(
            archive(
                tmp_path / "ambiguous.zip", {"S001_a.csv": content, "S002_a.csv": make_csv("S002")}
            ),
            ["walk"],
        )


def test_failure_guard_preserves_timeout_and_exception_atomically(tmp_path: Path) -> None:
    guard = RunGuard(tmp_path, input_hash="i", protocol_hash="p")
    guard.record_fold({"fold": 1})
    with pytest.raises(RuntimeError, match="boom"):
        guard.execute(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    failure = json.loads((tmp_path / "failure.json").read_text())
    assert failure["error"] == "boom"
    assert failure["completed_folds"] == [{"fold": 1}]
    assert failure["scientific_metrics"] is False
    with pytest.raises(Exception, match="fixed"):
        RunGuard(tmp_path / "other", input_hash="i", protocol_hash="p", timeout_seconds=1)
