"""Small deterministic CPU baseline for window-level HARTH features."""

from __future__ import annotations

from typing import cast

import numpy as np


class NearestCentroidBaseline:
    """A dependency-free baseline with probabilities from negative distances."""

    def fit(self, features: np.ndarray, labels: np.ndarray) -> NearestCentroidBaseline:
        x, y = np.asarray(features, float), np.asarray(labels, int)
        if x.ndim != 2 or y.ndim != 1 or len(x) != len(y) or len(x) == 0:
            raise ValueError("features and labels have incompatible shapes")
        self.classes_ = np.unique(y)
        self.centroids_ = np.vstack([x[y == cls].mean(axis=0) for cls in self.classes_])
        self.scale_ = np.maximum(x.std(axis=0), 1e-12)
        return self

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        if not hasattr(self, "centroids_"):
            raise RuntimeError("fit must be called before predict_proba")
        x = np.asarray(features, float)
        distances = -np.sum(
            ((x[:, None, :] - self.centroids_[None, :, :]) / self.scale_) ** 2, axis=2
        )
        distances -= distances.max(axis=1, keepdims=True)
        probabilities = np.exp(distances)
        return cast(np.ndarray, probabilities / probabilities.sum(axis=1, keepdims=True))

    def predict(self, features: np.ndarray) -> np.ndarray:
        return cast(np.ndarray, self.classes_[self.predict_proba(features).argmax(axis=1)])
