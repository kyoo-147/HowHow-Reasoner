"""Fail-closed statistical summaries for protocol-v2 fold output.

This module consumes canonical, already-computed per-subject fold rows.  It never
loads data or recomputes predictions, so its outputs are safe to exercise with
synthetic fixtures and cannot be mistaken for real metrics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import product
from typing import Any

import numpy as np

from .engine import BOOTSTRAP_REPS, ProtocolFailure

CONFIGURATIONS = ("full_sensor", "back_only", "thigh_only")
STATES = ("uncalibrated", "calibrated")
ESTIMANDS = ("nll", "brier", "ece", "accuracy", "macro_f1")
PRIMARY_ESTIMANDS = ("nll", "brier", "ece")


def _finite(value: Any) -> float:
    result = float(value)
    if not np.isfinite(result):
        raise ProtocolFailure("nonfinite statistical output")
    return result


def _validate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not rows:
        raise ProtocolFailure("no fold rows")
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        try:
            configuration, subject = str(row["configuration"]), str(row["test_subject"])
        except KeyError as exc:
            raise ProtocolFailure(f"missing fold field: {exc.args[0]}") from exc
        key = (configuration, subject)
        if configuration not in CONFIGURATIONS or key in indexed:
            raise ProtocolFailure("duplicate or unknown canonical fold row")
        indexed[key] = row
        for state in STATES:
            values = row.get(state)
            if not isinstance(values, Mapping) or subject not in values:
                raise ProtocolFailure(f"missing {state} population for {configuration}/{subject}")
            if set(map(str, values)) != {subject}:
                raise ProtocolFailure(
                    f"mismatched {state} subject population for {configuration}/{subject}"
                )
            metrics = values[subject]
            for metric in ESTIMANDS:
                if metric not in metrics:
                    raise ProtocolFailure(f"missing {metric} for {configuration}/{subject}/{state}")
                _finite(metrics[metric])
            if int(metrics.get("n", row.get("window_count", 0))) <= 0:
                raise ProtocolFailure("window counts must be positive")
    populations = {
        configuration: {subject for config, subject in indexed if config == configuration}
        for configuration in CONFIGURATIONS
    }
    if any(not values for values in populations.values()):
        raise ProtocolFailure("missing sensor-configuration fold population")
    expected = populations["full_sensor"]
    if any(values != expected for values in populations.values()):
        raise ProtocolFailure("mismatched subject populations across configurations")
    return indexed


def _percentile(values: Mapping[str, float], *, seed: int, alpha: float = 0.05) -> dict[str, Any]:
    if len(values) < 2:
        raise ProtocolFailure("subject-cluster intervals require at least two subjects")
    x = np.asarray([_finite(values[key]) for key in sorted(values)], dtype=float)
    rng = np.random.default_rng(seed)
    sampled = x[rng.integers(0, len(x), size=(BOOTSTRAP_REPS, len(x)))].mean(axis=1)
    return {
        "method": "subject-cluster-percentile-bootstrap",
        "repetitions": BOOTSTRAP_REPS,
        "seed": seed,
        "subjects": sorted(values),
        "estimate": float(x.mean()),
        "ci_95pct": [
            float(np.quantile(sampled, alpha / 2)),
            float(np.quantile(sampled, 1 - alpha / 2)),
        ],
    }


def _paired_pvalue(values: Mapping[str, float]) -> float:
    """Two-sided deterministic paired sign-flip test at the subject cluster level."""
    x = np.asarray([_finite(values[key]) for key in sorted(values)], dtype=float)
    if len(x) < 2:
        raise ProtocolFailure("paired comparisons require at least two subjects")
    observed = abs(float(x.mean()))
    count = 0
    total = 1 << len(x)
    if len(x) <= 20:
        for signs in product((-1.0, 1.0), repeat=len(x)):
            count += abs(float(np.mean(x * signs))) >= observed - 1e-15
    else:
        rng = np.random.default_rng(0)
        signs = rng.choice((-1.0, 1.0), size=(BOOTSTRAP_REPS, len(x)))
        count = int(np.sum(np.abs((signs * x).mean(axis=1)) >= observed - 1e-15))
        total = BOOTSTRAP_REPS
    return float(min(1.0, (count + 1) / (total + 1)))


def _holm(p_values: Mapping[str, float], alpha: float = 0.05) -> dict[str, Any]:
    ordered = sorted(
        ((name, _finite(value)) for name, value in p_values.items()),
        key=lambda item: (item[1], item[0]),
    )
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, value * (len(ordered) - rank)))
        adjusted[name] = running
    return {
        "alpha": alpha,
        "raw_p": dict(p_values),
        "adjusted_p": adjusted,
        "reject": {name: value <= alpha for name, value in adjusted.items()},
    }


def summarize_fold_rows(rows: Sequence[Mapping[str, Any]], *, seed: int = 0) -> dict[str, Any]:
    """Return complete macro estimates, cluster intervals, paired tests and diagnostics."""
    indexed = _validate_rows(rows)
    populations = {
        configuration: sorted(subject for config, subject in indexed if config == configuration)
        for configuration in CONFIGURATIONS
    }
    output: dict[str, Any] = {
        "status": "COMPLETE",
        "claim_boundary": "synthetic_or_supplied_only",
        "populations": populations,
        "estimands": list(ESTIMANDS),
        "configurations": {},
        "diagnostics": {
            "fold_count": len(rows),
            "missing_populations": [],
            "class_support": {},
            "window_counts": {},
            "temperature": {},
            "failures": [],
        },
    }
    for configuration in CONFIGURATIONS:
        configuration_out: dict[str, Any] = {"states": {}, "comparisons": {}}
        for state in STATES:
            subject_values = {
                metric: {
                    subject: _finite(indexed[(configuration, subject)][state][subject][metric])
                    for subject in populations[configuration]
                }
                for metric in ESTIMANDS
            }
            configuration_out["states"][state] = {
                metric: _percentile(values, seed=seed + ci * 100 + si * 10 + mi)
                for mi, (metric, values) in enumerate(subject_values.items())
                for ci, si in [(CONFIGURATIONS.index(configuration), STATES.index(state))]
            }
        for metric in ESTIMANDS:
            differences = {
                subject: _finite(indexed[(configuration, subject)]["calibrated"][subject][metric])
                - _finite(indexed[(configuration, subject)]["uncalibrated"][subject][metric])
                for subject in populations[configuration]
            }
            configuration_out["comparisons"][f"calibrated_minus_uncalibrated/{metric}"] = {
                "differences": differences,
                "interval": _percentile(differences, seed=seed + 1000 + ESTIMANDS.index(metric)),
                "p_value": _paired_pvalue(differences),
            }
        output["configurations"][configuration] = configuration_out
        for subject in populations[configuration]:
            row = indexed[(configuration, subject)]
            output["diagnostics"]["window_counts"][f"{configuration}/{subject}"] = int(
                row.get("window_count", 0)
            )
            output["diagnostics"]["temperature"][f"{configuration}/{subject}"] = {
                "selected": _finite(row.get("selected_temperature", 1.0)),
                "converged": bool(row.get("optimizer_converged", False)),
            }
            if "class_support" in row:
                output["diagnostics"]["class_support"][f"{configuration}/{subject}"] = row[
                    "class_support"
                ]
            if row.get("failures"):
                output["diagnostics"]["failures"].extend(row["failures"])
    primary = {
        metric: output["configurations"]["full_sensor"]["comparisons"][
            f"calibrated_minus_uncalibrated/{metric}"
        ]["p_value"]
        for metric in PRIMARY_ESTIMANDS
    }
    output["primary_calibration"] = _holm(primary)
    exploratory: dict[str, float] = {}
    for configuration in ("back_only", "thigh_only"):
        for state in STATES:
            for metric in ESTIMANDS:
                differences = {
                    subject: _finite(indexed[(configuration, subject)][state][subject][metric])
                    - _finite(indexed[("full_sensor", subject)][state][subject][metric])
                    for subject in populations[configuration]
                }
                key = f"{configuration}-vs-full/{state}/{metric}"
                output["configurations"][configuration]["comparisons"][key] = {
                    "differences": differences,
                    "interval": _percentile(differences, seed=seed + 2000 + len(exploratory)),
                    "p_value": _paired_pvalue(differences),
                }
                exploratory[key] = output["configurations"][configuration]["comparisons"][key][
                    "p_value"
                ]
    output["exploratory_ablation"] = _holm(exploratory)
    return output


analyze = summarize_fold_rows
