"""Bounded HARTH real-data pipeline with explicit provenance and failure records."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .baseline import NearestCentroidBaseline
from .download import download_harth
from .metrics import calibration_metrics
from .splits import assert_no_subject_leakage, subject_held_out_split

SENSORS = ("back_x", "back_y", "back_z", "thigh_x", "thigh_y", "thigh_z")
REQUIRED = {"timestamp", "label", "subject"}
_SUBJECT_RE = re.compile(r"(?:subject|subj|s)[_-]?(\d{1,3})", re.IGNORECASE)


@dataclass(frozen=True)
class FailureRecord:
    status: str
    failure_class: str
    message: str
    occurred_at_utc: str
    command: str
    data_path: str


@dataclass(frozen=True)
class RunManifest:
    status: str
    run_id: str
    started_at_utc: str
    finished_at_utc: str
    command: str
    dataset: dict[str, Any]
    limits: dict[str, int]
    split: dict[str, Any]
    extraction: dict[str, Any]
    metrics: dict[str, Any] | None
    artifacts: dict[str, str]
    scientific_claims: str = "none"


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_extract_zip(
    archive: Path,
    destination: Path,
    *,
    max_members: int = 200,
    max_member_bytes: int = 100_000_000,
    max_total_bytes: int = 1_000_000_000,
) -> list[Path]:
    """Extract only regular files beneath destination, with zip-bomb/path guards."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    total = 0
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if len(infos) > max_members:
            raise ValueError(f"archive has {len(infos)} members; limit is {max_members}")
        for info in infos:
            name = info.filename.replace("\\", "/")
            target = (destination / name).resolve()
            if (
                name.startswith("/")
                or ".." in Path(name).parts
                or target != destination
                and destination not in target.parents
            ):
                raise ValueError(f"unsafe archive member path: {info.filename}")
            if info.is_dir():
                continue
            if info.file_size > max_member_bytes or total + info.file_size > max_total_bytes:
                raise ValueError(f"archive member size exceeds configured bound: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as input_stream, target.open("wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            if target.stat().st_size != info.file_size:
                raise ValueError(f"extracted size mismatch: {info.filename}")
            total += info.file_size
            extracted.append(target)
    return extracted


def discover_csvs(root: Path) -> list[Path]:
    files = sorted(p for p in root.rglob("*.csv") if p.is_file())
    if not files:
        raise ValueError(f"no CSV files found below {root}")
    return files


def _subject_from_filename(path: Path) -> str | None:
    match = _SUBJECT_RE.search(path.stem)
    return f"S{int(match.group(1)):03d}" if match else None


def _normalise_subject(value: str) -> str:
    value = value.strip()
    match = re.search(r"\d+", value)
    return f"S{int(match.group(0)):03d}" if match else value


def load_windows(
    csvs: list[Path], *, max_rows: int, max_subjects: int, window_size: int, stride: int
) -> tuple[np.ndarray, np.ndarray, list[str], dict[str, Any]]:
    """Read a deterministic prefix of each file and produce majority-label windows."""
    if max_rows < 1 or max_subjects < 1 or window_size < 2 or stride < 1:
        raise ValueError("row, subject, window, and stride limits must be positive")
    rows: list[tuple[str, str, list[float]]] = []
    subjects: set[str] = set()
    files_used = 0
    rows_read = 0
    for path in csvs:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            fields = {str(field).strip().lower() for field in (reader.fieldnames or [])}
            # UCI HARTH identifies subjects in validated filenames (for example
            # S006.csv), so a subject column is optional for those files.
            fallback = _subject_from_filename(path)
            required = (REQUIRED - {"subject"}) | set(SENSORS)
            if fallback is None:
                required.add("subject")
            missing = required - fields
            if missing:
                raise ValueError(f"{path}: missing required columns {sorted(missing)}")
            file_rows = 0
            for raw in reader:
                if rows_read >= max_rows:
                    break
                subject = _normalise_subject(str(raw.get("subject", "") or fallback or ""))
                if not subject or subject == "S000":
                    raise ValueError(f"{path}: row has no parseable subject ID")
                if subject not in subjects and len(subjects) >= max_subjects:
                    continue
                try:
                    values = [float(raw[name]) for name in SENSORS]
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"{path}: non-numeric sensor row {rows_read + 1}") from exc
                label = str(raw["label"]).strip()
                if not label:
                    raise ValueError(f"{path}: blank label at row {rows_read + 1}")
                subjects.add(subject)
                rows.append((subject, label, values))
                rows_read += 1
                file_rows += 1
            files_used += int(file_rows > 0)
            if rows_read >= max_rows:
                break
    if not rows:
        raise ValueError("bounded read produced no rows")
    features: list[list[float]] = []
    labels: list[str] = []
    window_subjects: list[str] = []
    for subject in sorted(subjects):
        subject_rows = [row for row in rows if row[0] == subject]
        for start in range(0, max(0, len(subject_rows) - window_size + 1), stride):
            chunk = subject_rows[start : start + window_size]
            if len(chunk) < window_size:
                continue
            matrix = np.asarray([row[2] for row in chunk], dtype=float)
            features.append(np.concatenate((matrix.mean(0), matrix.std(0))))
            labels.append(Counter(row[1] for row in chunk).most_common(1)[0][0])
            window_subjects.append(subject)
    if not features:
        raise ValueError("bounded read produced no complete windows")
    return (
        np.asarray(features),
        np.asarray(labels),
        window_subjects,
        {
            "rows_read": rows_read,
            "subjects": sorted(subjects),
            "files_used": files_used,
            "window_count": len(features),
            "window_size": window_size,
            "stride": stride,
        },
    )


def _bootstrap_metrics(
    probabilities: np.ndarray, labels: np.ndarray, reps: int, seed: int
) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    values: dict[str, list[float]] = {key: [] for key in ("nll", "brier", "ece")}
    for _ in range(reps):
        indices = rng.integers(0, len(labels), size=len(labels))
        metrics = calibration_metrics(probabilities[indices], labels[indices])
        for key in values:
            values[key].append(float(metrics[key]))
    return {
        key: [float(np.quantile(value, 0.025)), float(np.quantile(value, 0.975))]
        for key, value in values.items()
    }


def run_smoke(args: argparse.Namespace) -> Path:
    started = datetime.now(UTC)
    started_clock = time.monotonic()
    output = Path(args.evidence_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    command = " ".join(sys.argv)
    run_id = started.strftime("harth-%Y%m%dT%H%M%SZ")
    failure_path = output / f"{run_id}.failure.json"
    manifest_path = output / f"{run_id}.manifest.json"
    limits: dict[str, int] = {
        "max_rows": args.max_rows,
        "max_subjects": args.max_subjects,
        "bootstrap": args.bootstrap,
        "timeout_seconds": args.timeout,
    }
    try:
        if time.monotonic() - started_clock > args.timeout:
            raise TimeoutError("timeout reached before download")
        archive = args.data_dir.resolve() / "harth.zip"
        download = download_harth(archive, deadline=started_clock + args.timeout)
        extraction_root = args.data_dir.resolve() / "extracted" / run_id
        safe_extract_zip(archive, extraction_root)
        features, raw_labels, subjects, extraction = load_windows(
            discover_csvs(extraction_root),
            max_rows=args.max_rows,
            max_subjects=args.max_subjects,
            window_size=args.window_size,
            stride=args.stride,
        )
        config = json.loads(args.config.read_text(encoding="utf-8"))
        split = subject_held_out_split(
            subjects, config["split"]["test_subjects"], seed=int(config["split"]["seed"])
        )
        assert_no_subject_leakage(split.train_subjects, split.test_subjects)
        train = np.asarray([s in split.train_subjects for s in subjects])
        test = np.asarray([s in split.test_subjects for s in subjects])
        classes = sorted(set(raw_labels[train]))
        if not np.all(np.isin(raw_labels[test], classes)):
            raise ValueError("held-out test contains labels absent from training subjects")
        label_map = {label: index for index, label in enumerate(classes)}
        y_train = np.asarray([label_map[label] for label in raw_labels[train]])
        y_test = np.asarray([label_map[label] for label in raw_labels[test]])
        model = NearestCentroidBaseline().fit(features[train], y_train)
        probabilities = model.predict_proba(features[test])
        metrics: dict[str, Any] = calibration_metrics(probabilities, y_test, bins=10)
        metrics["bootstrap_95pct"] = _bootstrap_metrics(probabilities, y_test, args.bootstrap, 0)
        metrics["classes"] = classes
        output_data = output / f"{run_id}.metrics.json"
        _json(output_data, metrics)
        finished = datetime.now(UTC)
        manifest = RunManifest(
            "INCONCLUSIVE",
            run_id,
            started.isoformat(),
            finished.isoformat(),
            command,
            {
                "url": download.url,
                "archive": download.destination,
                "sha256": download.sha256,
                "bytes": download.bytes,
            },
            limits,
            {
                "train_subjects": split.train_subjects,
                "test_subjects": split.test_subjects,
                "seed": split.seed,
            },
            extraction,
            metrics,
            {
                "metrics": str(output_data),
                "archive_manifest": str(
                    Path(download.destination).with_suffix(".zip.manifest.json")
                ),
            },
        )
        _json(manifest_path, asdict(manifest))
        return Path(manifest_path)
    except Exception as exc:
        failure = FailureRecord(
            "FAILED",
            type(exc).__name__,
            str(exc),
            datetime.now(UTC).isoformat(),
            command,
            str(args.data_dir),
        )
        _json(failure_path, asdict(failure))
        finished = datetime.now(UTC)
        manifest = RunManifest(
            "FAILED",
            run_id,
            started.isoformat(),
            finished.isoformat(),
            command,
            {},
            limits,
            {},
            {},
            None,
            {"failure": str(failure_path)},
        )
        _json(manifest_path, asdict(manifest))
        return Path(manifest_path)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("episodes/harth-calibration/data"))
    parser.add_argument(
        "--evidence-dir", type=Path, default=Path("episodes/harth-calibration/artifacts")
    )
    parser.add_argument(
        "--config", type=Path, default=Path("episodes/harth-calibration/episode.json")
    )
    parser.add_argument("--max-rows", type=int, default=100_000)
    parser.add_argument("--max-subjects", type=int, default=22)
    parser.add_argument("--bootstrap", type=int, default=200)
    parser.add_argument("--window-size", type=int, default=128)
    parser.add_argument("--stride", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main() -> None:
    args = parser().parse_args()
    if os.environ.get("HOWHOW_RUN_REAL_HARTH") != "1":
        raise SystemExit("real HARTH smoke is opt-in: set HOWHOW_RUN_REAL_HARTH=1")
    manifest = run_smoke(args)
    print(manifest)
    if json.loads(manifest.read_text(encoding="utf-8"))["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
