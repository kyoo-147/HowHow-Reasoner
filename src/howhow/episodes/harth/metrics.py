"""Dependency-free HARTH metrics and subject-cluster bootstrap intervals."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np


def _validate(probabilities: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(labels, dtype=int)
    if p.ndim != 2 or y.ndim != 1 or len(p) != len(y) or len(p) == 0:
        raise ValueError("probabilities must be non-empty 2-D and match labels")
    if np.any(~np.isfinite(p)) or np.any(p < 0) or not np.allclose(p.sum(axis=1), 1.0):
        raise ValueError("probabilities must be finite, non-negative rows summing to one")
    if np.any((y < 0) | (y >= p.shape[1])):
        raise ValueError("labels outside probability columns")
    return p, y


def calibration_metrics(
    probabilities: np.ndarray, labels: np.ndarray, bins: int = 10
) -> dict[str, float | int]:
    """Return NLL, multiclass Brier, and equal-width top-label ECE."""
    if bins < 2:
        raise ValueError("bins must be >= 2")
    p, y = _validate(probabilities, labels)
    n = len(y)
    clipped = np.clip(p[np.arange(n), y], np.finfo(float).tiny, 1.0)
    nll = float(-np.log(clipped).mean())
    one_hot = np.eye(p.shape[1])[y]
    brier = float(np.mean(np.sum((p - one_hot) ** 2, axis=1)))
    confidence = p.max(axis=1)
    correct = (p.argmax(axis=1) == y).astype(float)
    ece = 0.0
    for index in range(bins):
        lo, hi = index / bins, (index + 1) / bins
        mask = (confidence >= lo) & ((confidence < hi) if index < bins - 1 else (confidence <= hi))
        if np.any(mask):
            ece += float(mask.mean()) * abs(
                float(correct[mask].mean()) - float(confidence[mask].mean())
            )
    return {"nll": nll, "brier": brier, "ece": float(ece), "ece_bins": bins, "n": n}


def discrimination_metrics(probabilities: np.ndarray, labels: np.ndarray) -> dict[str, float]:
    """Return non-calibration discrimination metrics."""
    p, y = _validate(probabilities, labels)
    predicted = p.argmax(axis=1)
    classes = np.unique(y)
    f1s: list[float] = []
    for cls in classes:
        tp = float(np.sum((predicted == cls) & (y == cls)))
        fp = float(np.sum((predicted == cls) & (y != cls)))
        fn = float(np.sum((predicted != cls) & (y == cls)))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return {"accuracy": float(np.mean(predicted == y)), "macro_f1": float(np.mean(f1s))}


def per_subject_metrics(
    probabilities: np.ndarray, labels: np.ndarray, subjects: Sequence[str], bins: int = 10
) -> dict[str, dict[str, float | int]]:
    """Compute calibration metrics independently for each held-out subject."""
    if len(subjects) != len(labels):
        raise ValueError("subjects must match labels")
    result: dict[str, dict[str, float | int]] = {}
    for subject in dict.fromkeys(map(str, subjects)):
        mask = np.asarray([str(value) == subject for value in subjects])
        result[subject] = calibration_metrics(probabilities[mask], np.asarray(labels)[mask], bins)
    return result


def subject_cluster_bootstrap(
    probabilities: np.ndarray,
    labels: np.ndarray,
    subjects: Sequence[str],
    *,
    reps: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
    bins: int = 10,
) -> dict[str, object]:
    """Resample held-out subjects, retaining every window for each sampled cluster."""
    p, y = _validate(probabilities, labels)
    cluster_ids = np.asarray([str(value) for value in subjects])
    if len(cluster_ids) != len(y):
        raise ValueError("subjects must match labels")
    clusters = tuple(dict.fromkeys(cluster_ids))
    if len(clusters) < 2:
        raise ValueError("subject-cluster bootstrap requires at least two subjects")
    if reps < 100 or not 0 < alpha < 1:
        raise ValueError("reps must be >= 100 and alpha in (0, 1)")
    rng = np.random.default_rng(seed)
    estimates: dict[str, list[float]] = {key: [] for key in ("nll", "brier", "ece")}
    positions = {subject: np.flatnonzero(cluster_ids == subject) for subject in clusters}
    for _ in range(reps):
        sampled = rng.integers(0, len(clusters), size=len(clusters))
        indices = np.concatenate([positions[clusters[index]] for index in sampled])
        current = calibration_metrics(p[indices], y[indices], bins)
        for key in estimates:
            estimates[key].append(float(current[key]))
    intervals = {
        key: [float(np.quantile(value, alpha / 2)), float(np.quantile(value, 1 - alpha / 2))]
        for key, value in estimates.items()
    }
    return {
        "method": "subject-cluster percentile bootstrap",
        "repetitions": reps,
        "seed": seed,
        "cluster_count": len(clusters),
        "clusters": list(clusters),
        "per_subject_contributions": {
            subject: int(len(positions[subject])) for subject in clusters
        },
        "ci_95pct": intervals,
    }


def bootstrap_confidence_interval(
    values: Iterable[float],
    statistic: str = "mean",
    *,
    reps: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Legacy observation bootstrap retained for compatibility."""
    x = np.asarray(list(values), dtype=float)
    if x.ndim != 1 or len(x) < 2 or np.any(~np.isfinite(x)):
        raise ValueError("values must contain at least two finite observations")
    if reps < 100 or not 0 < alpha < 1:
        raise ValueError("reps must be >= 100 and alpha in (0, 1)")
    if statistic != "mean":
        raise ValueError("only mean is supported")
    rng = np.random.default_rng(seed)
    estimates = x[rng.integers(0, len(x), size=(reps, len(x)))].mean(axis=1)
    return float(np.quantile(estimates, alpha / 2)), float(np.quantile(estimates, 1 - alpha / 2))
