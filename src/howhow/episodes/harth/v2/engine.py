# ruff: noqa: E501
"""Adjudicated HARTH protocol-v2 execution engine.

The engine is deliberately deterministic and CPU-only.  It accepts already-windowed
records so the loader remains responsible for the physical file format and window
boundary checks.  No real-data runner is provided here; callers must explicitly
supply records and provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np

from ..metrics import calibration_metrics, discrimination_metrics
from .run_guard import RUN_TIMEOUT_SECONDS, atomic_write

PROTOCOL_ID = "harth-calibration-v2"
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 0
TEMPERATURE_BOUNDS = (0.05, 20.0)


class ProtocolFailure(ValueError):
    """A fail-closed protocol or provenance violation."""


@dataclass(frozen=True)
class Window:
    subject: str
    session: str
    label: str
    features: tuple[float, ...]
    provenance: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Window:
        required = ("subject", "session", "label", "features", "provenance")
        missing = [key for key in required if key not in value]
        if missing:
            raise ProtocolFailure(f"missing provenance fields: {missing}")
        return cls(
            str(value["subject"]),
            str(value["session"]),
            str(value["label"]),
            tuple(float(x) for x in value["features"]),
            str(value["provenance"]),
        )


@dataclass(frozen=True)
class Fold:
    test_subject: str
    train_subjects: tuple[str, ...]
    test_indices: tuple[int, ...]
    train_indices: tuple[int, ...]


@dataclass
class EngineResult:
    protocol_id: str
    status: str
    input_hash: str
    protocol_hash: str
    class_vocabulary: tuple[str, ...]
    folds: list[dict[str, Any]] = field(default_factory=list)
    configurations: dict[str, Any] = field(default_factory=dict)
    comparisons: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    code_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["class_vocabulary"] = list(self.class_vocabulary)
        return result


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def input_hash(windows: Sequence[Window]) -> str:
    return _digest(
        [
            asdict(item)
            for item in sorted(windows, key=lambda w: (w.provenance, w.subject, w.session, w.label))
        ]
    )


def protocol_hash(path: str | Path | None = None) -> str:
    if path is None:
        path = Path("episodes/harth-calibration/protocol/protocol-v2-proposal.json")
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def nested_loso_folds(windows: Sequence[Window]) -> tuple[Fold, ...]:
    subjects = tuple(sorted({w.subject for w in windows}))
    if len(subjects) < 2:
        raise ProtocolFailure("at least two eligible subjects are required")
    folds = []
    for test in subjects:
        test_indices = tuple(i for i, w in enumerate(windows) if w.subject == test)
        train_indices = tuple(i for i, w in enumerate(windows) if w.subject != test)
        folds.append(
            Fold(test, tuple(s for s in subjects if s != test), test_indices, train_indices)
        )
    return tuple(folds)


def _validate_windows(windows: Sequence[Window], classes: Sequence[str]) -> None:
    if not windows:
        raise ProtocolFailure("no eligible windows")
    if len(set(classes)) != len(tuple(classes)) or not classes:
        raise ProtocolFailure("class vocabulary must be non-empty and unique")
    seen: set[str] = set()
    dimensions: int | None = None
    for window in windows:
        if not window.subject or not window.session or not window.provenance:
            raise ProtocolFailure("incomplete provenance")
        if window.provenance in seen:
            raise ProtocolFailure("duplicate provenance record")
        seen.add(window.provenance)
        if window.label not in classes:
            raise ProtocolFailure(f"label outside frozen class vocabulary: {window.label}")
        if dimensions is None:
            dimensions = len(window.features)
        if len(window.features) != dimensions or not np.all(np.isfinite(window.features)):
            raise ProtocolFailure("invalid or inconsistent feature vector")


def _feature_indices(configuration: str, width: int) -> np.ndarray:
    if configuration == "full_sensor":
        return np.arange(width)
    if width % 2 or width < 2:
        raise ProtocolFailure("sensor ablations require mean/std features for two sensors")
    half = width // 2
    if configuration == "back_only":
        return np.arange(0, half)
    if configuration == "thigh_only":
        return np.arange(half, width)
    raise ProtocolFailure(f"unknown sensor configuration: {configuration}")


class _CentroidLogitModel:
    def __init__(self, classes: Sequence[str], indices: np.ndarray) -> None:
        self.classes = tuple(classes)
        self.indices = indices

    def fit(self, x: np.ndarray, y: np.ndarray) -> _CentroidLogitModel:
        if set(y.tolist()) != set(range(len(self.classes))):
            raise ProtocolFailure("class coverage failure in training fit")
        selected = x[:, self.indices]
        self.mean = selected.mean(axis=0)
        self.scale = np.maximum(selected.std(axis=0), 1e-12)
        self.centroids = np.vstack(
            [selected[y == c].mean(axis=0) for c in range(len(self.classes))]
        )
        return self

    def logits(self, x: np.ndarray) -> np.ndarray:
        selected = x[:, self.indices]
        return cast(
            np.ndarray,
            -np.sum(
                ((selected[:, None, :] - self.centroids[None, :, :]) / self.scale) ** 2, axis=2
            ),
        )


def _softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    z = np.asarray(logits, float) / temperature
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    p /= p.sum(axis=1, keepdims=True)
    if not np.all(np.isfinite(p)):
        raise ProtocolFailure("invalid probabilities")
    return p


def _nll(logits: np.ndarray, labels: np.ndarray, temperature: float) -> float:
    return float(
        -np.log(
            np.clip(_softmax(logits, temperature)[np.arange(len(labels)), labels], 1e-300, 1)
        ).mean()
    )


def fit_temperature(
    logits: np.ndarray,
    labels: np.ndarray,
    *,
    bounds: tuple[float, float] = TEMPERATURE_BOUNDS,
    tolerance: float = 1e-8,
    max_iterations: int = 128,
) -> tuple[float, bool]:
    """Deterministic bounded golden-section minimization; ties choose smaller T."""
    lo, hi = map(float, bounds)
    if not 0 < lo <= hi or not len(labels) or np.any(~np.isfinite(logits)):
        raise ProtocolFailure("invalid temperature optimization input")
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = _nll(logits, labels, c), _nll(logits, labels, d)
    converged = False
    for _ in range(max_iterations):
        if b - a <= tolerance:
            converged = True
            break
        if fc <= fd:  # <= is the deterministic smaller-T tie break
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = _nll(logits, labels, c)
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = _nll(logits, labels, d)
    candidates = [
        (a, _nll(logits, labels, a)),
        ((a + b) / 2, _nll(logits, labels, (a + b) / 2)),
        (b, _nll(logits, labels, b)),
    ]
    return min(candidates, key=lambda item: (item[1], item[0]))[0], converged


def holm_correction(p_values: Mapping[str, float], alpha: float = 0.05) -> dict[str, Any]:
    ordered = sorted(
        ((str(k), float(v)) for k, v in p_values.items()), key=lambda item: (item[1], item[0])
    )
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * value))
        adjusted[name] = running
    return {
        "alpha": alpha,
        "adjusted_p": adjusted,
        "reject": {k: v <= alpha for k, v in adjusted.items()},
    }


def paired_subject_bootstrap(
    differences: Mapping[str, float], *, reps: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED
) -> dict[str, Any]:
    if len(differences) < 2 or reps != BOOTSTRAP_REPS:
        raise ProtocolFailure(
            "paired bootstrap requires at least two subjects and exactly 2000 resamples"
        )
    keys = tuple(sorted(differences))
    values = np.asarray([differences[k] for k in keys], float)
    rng = np.random.default_rng(seed)
    samples = values[rng.integers(0, len(values), size=(reps, len(values)))].mean(axis=1)
    return {
        "method": "subject-cluster-paired-bootstrap",
        "repetitions": reps,
        "seed": seed,
        "subjects": list(keys),
        "mean": float(values.mean()),
        "ci_95pct": [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
    }


def _inner_logits(
    x: np.ndarray,
    y: np.ndarray,
    subjects: Sequence[str],
    classes: Sequence[str],
    indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    all_logits: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    unique = tuple(sorted(set(subjects)))
    if len(unique) < 2:
        raise ProtocolFailure("fewer than two inner subjects for temperature calibration")
    for held in unique:
        train = np.asarray([s != held for s in subjects])
        valid = ~train
        if set(y[train].tolist()) != set(range(len(classes))):
            raise ProtocolFailure("class coverage failure in inner calibration fold")
        model = _CentroidLogitModel(classes, indices).fit(x[train], y[train])
        all_logits.append(model.logits(x[valid]))
        all_labels.append(y[valid])
    return np.vstack(all_logits), np.concatenate(all_labels)


def _uncertainty(
    probabilities: np.ndarray, labels: np.ndarray, *, seed: int
) -> dict[str, list[float]]:
    """Finite observation-level uncertainty for the schema handoff."""
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(labels, dtype=int)
    n = len(y)
    if n < 2:
        raise ProtocolFailure("uncertainty requires at least two held-out windows")
    correct = (p.argmax(axis=1) == y).astype(float)
    confidence = p.max(axis=1)
    losses = -np.log(np.clip(p[np.arange(n), y], 1e-300, 1.0))
    brier = np.sum((p - np.eye(p.shape[1])[y]) ** 2, axis=1)
    ece_contribution = np.abs(correct - confidence)
    values = (losses, brier, ece_contribution)
    intervals: dict[str, list[float]] = {}
    for name, value in zip(("nll", "brier", "ece"), values, strict=True):
        mean = float(np.mean(value))
        half = 1.96 * float(np.std(value, ddof=1)) / np.sqrt(n)
        # Constant finite samples receive a strict representable bound.
        half = max(half, np.finfo(float).eps * max(1.0, abs(mean)))
        intervals[name] = [max(0.0, mean - half), mean + half]
    return intervals


def _subject_metric_rows(
    probabilities: np.ndarray, labels: np.ndarray, subjects: Sequence[str], classes: Sequence[str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for subject in sorted(set(subjects)):
        mask = np.asarray([s == subject for s in subjects])
        metrics: dict[str, Any] = dict(calibration_metrics(probabilities[mask], labels[mask]))
        metrics.update(discrimination_metrics(probabilities[mask], labels[mask]))
        metrics["subject"] = subject
        metrics["class_support"] = {
            str(cls): int(np.sum(labels[mask] == cls)) for cls in range(len(classes))
        }
        metrics["intervals"] = _uncertainty(probabilities[mask], labels[mask], seed=0)
        result[subject] = metrics
    return result


def _comparison_report(folds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Build paired subject comparisons without treating windows as replicates."""
    report: dict[str, Any] = {}
    primary_p: dict[str, float] = {}
    for configuration in ("full_sensor", "back_only", "thigh_only"):
        rows = [row for row in folds if row["configuration"] == configuration]
        if len(rows) < 2:
            continue
        state_report: dict[str, Any] = {}
        for metric in ("nll", "brier", "ece"):
            differences = {
                row["test_subject"]: float(
                    row["calibrated"][row["test_subject"]][metric]
                    - row["uncalibrated"][row["test_subject"]][metric]
                )
                for row in rows
            }
            state_report[metric] = {
                "calibrated_minus_uncalibrated": differences,
                "bootstrap": paired_subject_bootstrap(differences),
            }
            if configuration == "full_sensor":
                nonzero = [value for value in differences.values() if value != 0]
                primary_p[metric] = (
                    1.0
                    if not nonzero
                    else min(
                        1.0,
                        2.0
                        * min(
                            sum(value <= 0 for value in nonzero),
                            sum(value >= 0 for value in nonzero),
                        )
                        / len(nonzero),
                    )
                )
        report[configuration] = state_report
    report["holm_primary"] = holm_correction(primary_p)
    return report


def run_protocol(
    windows: Iterable[Window | Mapping[str, Any]],
    classes: Sequence[str],
    *,
    protocol_file: str | Path | None = None,
    checkpoint: str | Path | None = None,
    timeout_seconds: float = 1800.0,
    monotonic: Callable[[], float] | None = None,
    code_hash: str | None = None,
    fold_callback: Callable[[str], None] | None = None,
    checkpoint_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> EngineResult:
    """Execute synthetic or explicitly supplied windows under nested LOSO.

    A checkpoint is written after each completed sensor configuration/fold and is
    only resumed when its immutable input and protocol hashes match.
    """
    records = tuple(
        item if isinstance(item, Window) else Window.from_mapping(item) for item in windows
    )
    vocabulary = tuple(str(c) for c in classes)
    _validate_windows(records, vocabulary)
    ihash, phash = input_hash(records), protocol_hash(protocol_file)
    clock = time.monotonic if monotonic is None else monotonic
    if timeout_seconds != RUN_TIMEOUT_SECONDS:
        raise ProtocolFailure("timeout budget is fixed at 1800 seconds")
    deadline = clock() + RUN_TIMEOUT_SECONDS
    result = EngineResult(
        PROTOCOL_ID,
        "RUNNING",
        ihash,
        phash,
        vocabulary,
        manifest={"real_rerun": False, "claim_boundary": "synthetic_or_supplied_only"},
        code_hash=code_hash,
    )
    completed: set[tuple[str, str]] = set()
    if checkpoint and Path(checkpoint).exists():
        saved = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
        if saved.get("input_hash") != ihash or saved.get("protocol_hash") != phash:
            raise ProtocolFailure("checkpoint immutable hash mismatch")
        result.folds = saved.get("folds", [])
        completed = {(row["configuration"], row["test_subject"]) for row in result.folds}
    x = np.asarray([w.features for w in records], float)
    y = np.asarray([vocabulary.index(w.label) for w in records], int)
    subjects = [w.subject for w in records]
    folds = nested_loso_folds(records)
    for configuration in ("full_sensor", "back_only", "thigh_only"):
        indices = _feature_indices(configuration, x.shape[1])
        for fold in folds:
            if (configuration, fold.test_subject) in completed:
                continue
            if clock() >= deadline:
                result.status, result.failures = "FAILED", ["timeout"]
                raise ProtocolFailure("timeout")
            train, test = np.asarray(fold.train_indices), np.asarray(fold.test_indices)
            if set(y[train]) != set(range(len(vocabulary))):
                raise ProtocolFailure("class coverage failure in outer training fold")
            base = _CentroidLogitModel(vocabulary, indices).fit(x[train], y[train])
            logits = base.logits(x[test])
            temperature, converged = fit_temperature(
                *_inner_logits(
                    x[train], y[train], [subjects[i] for i in train], vocabulary, indices
                )
            )
            probabilities = _softmax(logits, temperature)
            uncalibrated = _softmax(logits)
            fold_row = {
                "configuration": configuration,
                "test_subject": fold.test_subject,
                "train_subjects": list(fold.train_subjects),
                "window_count": len(test),
                "selected_temperature": temperature,
                "optimizer_converged": converged,
                "uncalibrated": _subject_metric_rows(
                    uncalibrated, y[test], [subjects[i] for i in test], vocabulary
                ),
                "calibrated": _subject_metric_rows(
                    probabilities, y[test], [subjects[i] for i in test], vocabulary
                ),
                "calibration_state": ["uncalibrated", "calibrated"],
            }
            result.folds.append(fold_row)
            if configuration == "full_sensor" and fold_callback is not None:
                fold_callback(fold.test_subject)
            if checkpoint_callback is not None:
                checkpoint_callback(result.to_dict())
            elif checkpoint:
                atomic_write(Path(checkpoint), result.to_dict())
    result.comparisons = _comparison_report(result.folds)
    result.status = "COMPLETE"
    if checkpoint_callback is None and checkpoint:
        atomic_write(Path(checkpoint), result.to_dict())
    return result


# Public aliases used by callers and tests.
execute = run_protocol
build_nested_loso_folds = nested_loso_folds
optimize_temperature = fit_temperature
