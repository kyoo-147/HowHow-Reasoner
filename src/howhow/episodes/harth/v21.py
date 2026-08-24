"""HARTH protocol-v2.1 support-aware, synthetic-fixture-safe contracts.

This module contains only deterministic estimators and artifact contracts.  It
never loads or consumes a completed v2 checkpoint; callers must provide
window-level observations explicitly.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np

PROTOCOL_VERSION = "protocol-v2.1"
SCHEMA_VERSION = "result-schema-v2.1"
P_FLOOR = 1e-12
SUM_TOLERANCE = 1e-12
ECE_EDGES = tuple(i / 10 for i in range(11))
ECE_SPEC = {
    "bins": 10,
    "edges": list(ECE_EDGES),
    "intervals": "first_nine_left_closed_right_open_final_closed",
    "tie_rule": "first_canonical_argmax",
    "sufficient_statistics": ["count", "confidence_sum", "correct_sum"],
}
VOCABULARY_SIZE = 12
BOOTSTRAP_REPS = 2000
MIN_VALID_REPLICATES = 1900
PVALUE_DRAWS = 200000
MIN_PAIRED_SUBJECTS = 20
HOLM_IDS = ("H_NLL", "H_BRIER", "H_ECE")


class V21Error(ValueError):
    pass


class NotEstimable(V21Error):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def ece_spec_hash() -> str:
    return canonical_hash(ECE_SPEC)


def _finite(x: Any, name: str) -> float:
    if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(float(x)):
        raise V21Error(f"NONFINITE_{name.upper()}")
    return float(x)


def validate_probabilities(
    probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> np.ndarray:
    p = np.asarray(probabilities, dtype=float)
    if p.ndim != 2 or p.shape[1] != len(classes) or not len(classes):
        raise V21Error("PROBABILITY_DOMAIN")
    if (
        not np.all(np.isfinite(p))
        or np.any(p < 0)
        or np.any(p > 1)
        or np.any(np.abs(p.sum(axis=1) - 1.0) > SUM_TOLERANCE)
    ):
        raise V21Error("PROBABILITY_DOMAIN")
    return p


def support_gate(
    labels: Sequence[str], classes: Sequence[str], *, stage: str, minimum: int
) -> dict[str, Any]:
    counts = {c: int(sum(label == c for label in labels)) for c in classes}
    if stage not in {"training", "inner_calibration", "held_out_test"}:
        raise V21Error("unknown support stage")
    if stage == "held_out_test":
        return {
            "stage": stage,
            "counts": counts,
            "status": "PASS",
            "zero_support": [c for c, n in counts.items() if n == 0],
        }
    failed = [c for c, n in counts.items() if n < minimum]
    return {
        "stage": stage,
        "counts": counts,
        "minimum": minimum,
        "status": "FAILED" if failed else "PASS",
        "reason": ("TRAINING_CLASS_SUPPORT" if stage == "training" else "INNER_CLASS_SUPPORT")
        if failed
        else None,
        "failed_classes": failed,
    }


def _rows(
    labels: Sequence[str], probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> tuple[np.ndarray, np.ndarray]:
    p = validate_probabilities(probabilities, classes)
    if len(labels) != len(p) or not len(labels):
        raise V21Error("ZERO_WINDOWS")
    try:
        y = np.asarray([classes.index(label) for label in labels], dtype=int)
    except ValueError as exc:
        raise V21Error("INVALID_LABEL") from exc
    return y, p


def subject_metrics(
    labels: Sequence[str], probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> dict[str, Any]:
    y, p = _rows(labels, probabilities, classes)
    n = len(y)
    nll = float(-np.log(np.maximum(p[np.arange(n), y], P_FLOOR)).mean())
    brier = float(np.mean(np.sum((p - np.eye(len(classes))[y]) ** 2, axis=1)))
    confidence = p.max(axis=1)
    predicted = p.argmax(axis=1)
    bins = []
    ece = 0.0
    for b, left in enumerate(ECE_EDGES[:-1]):
        right = ECE_EDGES[b + 1]
        mask = (confidence >= left) & ((confidence < right) if b < 9 else (confidence <= right))
        count = int(mask.sum())
        cs = float(confidence[mask].sum())
        rs = int((predicted[mask] == y[mask]).sum())
        bins.append({"bin": b + 1, "count": count, "confidence_sum": cs, "correct_sum": rs})
        if count:
            ece += count / n * abs(rs / count - cs / count)
    return {
        "n": n,
        "nll": nll,
        "brier": brier,
        "ece": float(ece),
        "ece_spec_hash": ece_spec_hash(),
        "p_floor": P_FLOOR,
        "probability_domain": "finite_[0,1]_sum_tolerance_1e-12",
        "sum_tolerance": SUM_TOLERANCE,
        "log_base": "natural",
        "probability_input": "raw_for_brier_ece_floored_for_nll",
        "ece_bins": bins,
    }


def subject_macro(
    subject_records: Mapping[str, Mapping[str, Any]], metric: str, *, min_windows: int = 1
) -> dict[str, Any]:
    eligible, excluded = [], []
    for subject in subject_records:
        n = int(subject_records[subject].get("n", 0))
        if n < min_windows:
            excluded.append({"subject_id": subject, "reason": "ZERO_WINDOWS"})
        else:
            value = subject_records[subject].get(metric)
            if value is None or not math.isfinite(float(value)):
                excluded.append({"subject_id": subject, "reason": "ESTIMAND_NOT_ESTIMABLE"})
            else:
                eligible.append(float(value))
    if not eligible:
        return {
            "status": "NOT_ESTIMABLE",
            "reason": "NO_ELIGIBLE_SUBJECTS",
            "scope": "subject_macro",
            "excluded": excluded,
        }
    return {
        "status": "ESTIMABLE",
        "value": float(np.mean(eligible)),
        "scope": "subject_macro",
        "eligible_subject_count": len(eligible),
        "excluded": excluded,
    }


def f1_report(
    labels: Sequence[str], probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> dict[str, Any]:
    y, p = _rows(labels, probabilities, classes)
    pred = p.argmax(axis=1)
    rows: list[dict[str, Any]] = []
    observed: list[str] = []
    for k, cls in enumerate(classes):
        tp = int(np.sum((pred == k) & (y == k)))
        fp = int(np.sum((pred == k) & (y != k)))
        fn = int(np.sum((pred != k) & (y == k)))
        support = tp + fn
        observed.append(cls) if support else None
        precision = (
            {"status": "ESTIMABLE", "value": tp / (tp + fp)}
            if tp + fp
            else {"status": "NOT_ESTIMABLE", "reason": "ZERO_PREDICTED_POSITIVES"}
        )
        recall = (
            {"status": "ESTIMABLE", "value": tp / support}
            if support
            else {"status": "NOT_ESTIMABLE", "reason": "ZERO_TRUE_SUPPORT"}
        )
        denom = 2 * tp + fp + fn
        f1 = (
            {"status": "ESTIMABLE", "value": 2 * tp / denom}
            if denom
            else {"status": "NOT_ESTIMABLE", "reason": "ZERO_F1_DENOMINATOR"}
        )
        rows.append(
            {
                "class": cls,
                "TP": tp,
                "FP": fp,
                "FN": fn,
                "support": support,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    observed_values = [r["f1"]["value"] for r in rows if r["class"] in observed]
    fixed_bad = [r["class"] for r in rows if r["f1"]["status"] != "ESTIMABLE"]
    return {
        "classes": rows,
        "K_obs": observed,
        "observed_macro_f1": {
            "status": "ESTIMABLE",
            "value": float(np.mean(observed_values)),
            "denominator": len(observed),
        }
        if observed
        else {"status": "NOT_ESTIMABLE", "reason": "NO_OBSERVED_CLASSES"},
        "fixed_vocabulary_macro_f1": {
            "status": "NOT_ESTIMABLE",
            "reason": "ZERO_F1_DENOMINATOR",
            "classes": fixed_bad,
        }
        if fixed_bad
        else {
            "status": "ESTIMABLE",
            "value": float(np.mean([r["f1"]["value"] for r in rows])),
            "denominator": len(classes),
        },
    }


def job_seed(job_id: str) -> tuple[str, int]:
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return digest, int(digest, 16)


def frozen_quantile(values: Sequence[float], q: float) -> float:
    x = np.sort(np.asarray(values, dtype=float))
    h = (len(x) - 1) * q
    i = math.floor(h)
    j = math.ceil(h)
    return float(x[i] + (h - i) * (x[j] - x[i]))


def bootstrap(
    subject_values: Mapping[str, float], *, job_id: str, reps: int = BOOTSTRAP_REPS
) -> dict[str, Any]:
    if reps != BOOTSTRAP_REPS:
        raise V21Error("BOOTSTRAP_REPLICATES")
    digest, seed = job_seed(job_id)
    keys = tuple(subject_values)
    vals = np.asarray([_finite(subject_values[k], "metric") for k in keys])
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = vals[rng.integers(0, len(vals), size=(reps, len(vals)))].mean(axis=1)
    finite = draws[np.isfinite(draws)]
    out = {
        "job_id": job_id,
        "job_sha256": digest,
        "unsigned_seed": seed,
        "generator": "PCG64",
        "replicates": reps,
        "valid_replicates": int(len(finite)),
        "invalid_replicates": int(reps - len(finite)),
        "min_valid_replicates": MIN_VALID_REPLICATES,
        "quantile_formula": "h=(n-1)q;i=floor(h);j=ceil(h);Q=x[i]+(h-i)(x[j]-x[i])",
    }
    if len(finite) < MIN_VALID_REPLICATES:
        return {
            **out,
            "status": "NOT_ESTIMABLE",
            "reason": "BOOTSTRAP_VALID_REPLICATES_BELOW_THRESHOLD",
        }
    return {
        **out,
        "status": "ESTIMABLE",
        "estimate": float(vals.mean()),
        "ci_95": [frozen_quantile(finite, 0.025), frozen_quantile(finite, 0.975)],
    }


def pvalue(differences: Mapping[str, float], *, estimand: str) -> dict[str, Any]:
    job_id = f"{PROTOCOL_VERSION}|pvalue|{estimand}|calibrated_vs_uncalibrated|seed=0"
    digest, seed = job_seed(job_id)
    keys = tuple(differences)
    d = np.asarray([_finite(differences[k], "difference") for k in keys])
    base = float(d.mean())
    if len(d) < MIN_PAIRED_SUBJECTS:
        return {
            "job_id": job_id,
            "status": "NOT_ESTIMABLE",
            "reason": "INSUFFICIENT_PAIRED_SUBJECTS",
            "eligible_subject_count": len(d),
        }
    rng = np.random.Generator(np.random.PCG64(seed))
    signs = rng.choice(np.array([-1.0, 1.0]), size=(PVALUE_DRAWS, len(d)))
    count = int(np.sum((signs * d).mean(axis=1) <= base))
    return {
        "job_id": job_id,
        "job_sha256": digest,
        "unsigned_seed": seed,
        "generator": "PCG64",
        "T_obs": base,
        "draws": PVALUE_DRAWS,
        "p_value": (1 + count) / (PVALUE_DRAWS + 1),
        "status": "ESTIMABLE",
        "alternative": "calibrated_lower",
        "tie_rule": "inclusive_leq",
        "zero_difference_rule": "unchanged",
    }


def holm(pvalues: Mapping[str, float], *, alpha: float = 0.05) -> dict[str, Any]:
    if tuple(sorted(pvalues)) != tuple(sorted(HOLM_IDS)) or any(
        not math.isfinite(float(v)) for v in pvalues.values()
    ):
        return {"status": "INCOMPLETE_FAMILY", "hypotheses": [], "alpha": alpha, "m": 3}
    order = sorted(HOLM_IDS, key=lambda x: (float(pvalues[x]), HOLM_IDS.index(x)))
    rows: list[dict[str, Any]] = []
    stop = None
    adjusted = []
    for rank, ident in enumerate(order, 1):
        p = float(pvalues[ident])
        threshold = alpha / (4 - rank)
        local = p <= threshold
        if stop is None and not local:
            stop = rank
        adjusted.append(min(1.0, (4 - rank) * p))
        rows.append(
            {
                "identifier": ident,
                "raw_p": p,
                "sorted_rank": rank,
                "equality_tie_rank": sum(float(pvalues[x]) == p for x in order[:rank]),
                "threshold": threshold,
                "raw_holm": adjusted[-1],
                "local_pass": local,
            }
        )
    running = 0.0
    by: dict[str, float] = {}
    for row, raw in zip(rows, adjusted, strict=True):
        running = max(running, raw)
        by[row["identifier"]] = running
    for row in rows:
        row["adjusted_p"] = by[row["identifier"]]
        row["final_reject"] = stop is None or row["sorted_rank"] < stop
        row["stop_rank"] = stop
    return {
        "status": "COMPLETE",
        "family_id": "v2-primary-calibrated-vs-uncalibrated-3",
        "alpha": alpha,
        "m": 3,
        "sort_rule": "ascending_raw_p",
        "tie_rule": list(HOLM_IDS),
        "hypotheses": rows,
    }


def window_set_hash(windows: Sequence[Sequence[Any]]) -> str:
    return canonical_hash(list(windows))


def migration_v2_to_v21(source: Mapping[str, Any]) -> dict[str, Any]:
    if source.get("schema_version") == "harth-result-v1" or str(
        source.get("schema_version", "")
    ).endswith("v2"):
        raise V21Error("REJECT_V2_RELABELING_OR_METRIC_MIGRATION")
    raise V21Error("migration requires explicit structural v2 source and unavailable v2.1 fields")


def atomic_canonical_write(path: str | Path, value: Any) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value)
    digest = sha256_bytes(data)
    if target.exists():
        raise V21Error("IMMUTABLE_OUTPUT_EXISTS")
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if sha256_bytes(Path(tmp).read_bytes()) != digest:
            raise V21Error("ATOMIC_HASH_MISMATCH")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return digest


STATES = (
    "DECLARED",
    "PREFLIGHT_PASS",
    "LOADED",
    "TRAINING_PASS",
    "INNER_CALIBRATION_PASS",
    "OUTER_TEST_OBSERVED",
    "METRICS_READY",
    "AGGREGATED",
    "INFERENTIAL_READY",
    "COMPLETE",
    "FAILED",
    "NOT_ESTIMABLE",
    "INCOMPLETE_FAMILY",
)
_TRANSITIONS = {a: b for a, b in zip(STATES[:9], STATES[1:10], strict=True)}


def transition(
    current: str,
    target: str,
    *,
    scope: str = "run",
    reason: str | None = None,
    required_fields_missing: Sequence[str] = (),
) -> dict[str, Any]:
    if target in {"FAILED", "NOT_ESTIMABLE", "INCOMPLETE_FAMILY"}:
        if target == "FAILED" and not reason:
            raise V21Error("FAILED requires reason")
        return {
            "state": target,
            "scope": scope,
            "reason": reason,
            "required_fields_missing": list(required_fields_missing),
        }
    if _TRANSITIONS.get(current) != target:
        raise V21Error(f"invalid transition {current}->{target}")
    return {"state": target, "scope": scope, "required_fields_missing": []}


def pairing_manifest(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        (dict(r) for r in records),
        key=lambda r: (
            str(r.get("subject_id", "")),
            str(r.get("contrast_id", "")),
            str(r.get("estimand_id", "")),
            str(r.get("arm", "")),
        ),
    )
    for row in ordered:
        required = {
            "subject_id",
            "contrast_id",
            "estimand_id",
            "reason",
            "arm",
            "window_set_hash",
            "population_rule_id",
        }
        if set(row) < required:
            raise V21Error("pairing manifest missing fields")
    eligible = [r for r in ordered if not r["reason"]]
    return {
        "records": ordered,
        "pairing_manifest_hash": canonical_hash(ordered),
        "eligible_subject_hash": canonical_hash([r["subject_id"] for r in eligible]),
        "population_rule_id": "subject_macro_min_windows_1_metric_specific",
    }


REQUIRED_HASH_FIELDS = (
    "protocol_sha256",
    "schema_sha256",
    "config_sha256",
    "code_sha256",
    "input_sha256",
    "vocabulary_sha256",
)


def validate_result(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the versioned structural handoff without accepting v2 metrics."""
    if (
        not isinstance(data, Mapping)
        or data.get("schema_version") != SCHEMA_VERSION
        or data.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise V21Error("unsupported result-schema-v2.1")
    status = data.get("status")
    if status not in {"COMPLETE", "FAILED", "NOT_ESTIMABLE", "INCOMPLETE_FAMILY"}:
        raise V21Error("invalid v2.1 status")
    hashes = data.get("hashes")
    if not isinstance(hashes, Mapping) or any(
        not isinstance(hashes.get(name), str)
        or len(cast(str, hashes[name])) != 64
        or any(c not in "0123456789abcdef" for c in cast(str, hashes[name]))
        for name in REQUIRED_HASH_FIELDS
    ):
        raise V21Error("missing required v2.1 hashes")
    state = data.get("state")
    if state not in STATES:
        raise V21Error("invalid state-machine status")
    if status == "COMPLETE" and state != "COMPLETE":
        raise V21Error("COMPLETE result must be in COMPLETE state")
    return cast(dict[str, Any], json.loads(json.dumps(dict(data), ensure_ascii=False)))
