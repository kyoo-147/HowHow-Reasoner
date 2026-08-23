"""Dependency-free calibration metrics and deterministic bootstrap intervals."""

from __future__ import annotations

from collections.abc import Iterable

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
    """Return NLL, multiclass Brier, and equal-width top-label ECE.

    ECE uses ``bins`` equal-width intervals on [0, 1], with the right endpoint
    included in the final bin. Empty bins contribute zero.
    """
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


def bootstrap_confidence_interval(
    values: Iterable[float],
    statistic: str = "mean",
    *,
    reps: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap CI; deterministic and intentionally preserves failures."""
    x = np.asarray(list(values), dtype=float)
    if x.ndim != 1 or len(x) < 2 or np.any(~np.isfinite(x)):
        raise ValueError("values must contain at least two finite observations")
    if reps < 100 or not 0 < alpha < 1:
        raise ValueError("reps must be >= 100 and alpha in (0, 1)")
    if statistic != "mean":
        raise ValueError("only mean is supported")
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, len(x), size=(reps, len(x)))
    estimates = x[samples].mean(axis=1)
    return float(np.quantile(estimates, alpha / 2)), float(np.quantile(estimates, 1 - alpha / 2))
