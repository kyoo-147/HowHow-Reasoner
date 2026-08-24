"""Canonical HARTH protocol-v2.1 support-aware contract surface.

Only explicitly supplied synthetic/window observations are accepted.  This module
never opens or consumes completed v2 checkpoint metric fields.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError

PROTOCOL_VERSION = "protocol-v2.1"
APPROVED_PROPOSAL_SHA256 = "17cfe84a5096ce1025f13cce779e34a55ab459f408168c899b2de05b2c339b08"
APPROVED_DECISION_SHA256 = "7469a71ac40a002595a0e2a5d241a62ddd8278cb5bb507a529a2fb579d12061c"
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
BOOTSTRAP_REPS = 2000
MIN_VALID_REPLICATES = 1900
PVALUE_DRAWS = 200000
MIN_PAIRED_SUBJECTS = 20
HOLM_IDS = ("H_NLL", "H_BRIER", "H_ECE")
ESTIMANDS = ("nll", "brier", "ece")
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
SCOPES = ("run", "configuration", "state", "subject", "fold", "estimand", "family")
_HASHES = (
    "protocol_sha256",
    "schema_sha256",
    "config_sha256",
    "code_sha256",
    "input_sha256",
    "vocabulary_sha256",
    "eligibility_manifest_sha256",
    "pairing_manifest_sha256",
)


class V21Error(ValueError):
    """Fail-closed v2.1 contract violation."""


class NotEstimable(V21Error):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def ece_spec_hash() -> str:
    return canonical_hash(ECE_SPEC)


def _finite(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise V21Error(f"NONFINITE_{name.upper()}")
    return float(value)


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise V21Error(f"INVALID_{name.upper()}")
    return value


def validate_probabilities(
    probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> np.ndarray:
    p = np.asarray(probabilities, dtype=float)
    if (
        p.ndim != 2
        or p.shape[1] != len(classes)
        or not len(classes)
        or not np.all(np.isfinite(p))
        or np.any(p < 0)
        or np.any(p > 1)
        or np.any(np.abs(p.sum(axis=1) - 1.0) > SUM_TOLERANCE)
    ):
        raise V21Error("PROBABILITY_DOMAIN")
    return p


def support_gate(
    labels: Sequence[str], classes: Sequence[str], *, stage: str, minimum: int
) -> dict[str, Any]:
    if stage not in {"training", "inner_calibration", "held_out_test"} or minimum < 1:
        raise V21Error("INVALID_SUPPORT_GATE")
    counts = {c: int(sum(label == c for label in labels)) for c in classes}
    if stage == "held_out_test":
        return {
            "stage": stage,
            "counts": counts,
            "status": "OUTER_TEST_OBSERVED",
            "class_status": {
                c: (
                    {"status": "NOT_ESTIMABLE", "reason": "ZERO_SUPPORT", "support": 0}
                    if n == 0
                    else {"status": "OBSERVED", "support": n}
                )
                for c, n in counts.items()
            },
            "zero_support": [c for c, n in counts.items() if n == 0],
            "aggregate_metrics_allowed": True,
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
    if not labels:
        raise V21Error("ZERO_WINDOWS")
    try:
        y = np.asarray([classes.index(label) for label in labels], dtype=int)
    except ValueError as exc:
        raise V21Error("INVALID_LABEL") from exc
    p = validate_probabilities(probabilities, classes)
    if len(labels) != len(p):
        raise V21Error("WINDOW_LABEL_PROBABILITY_MISMATCH")
    return y, p


def subject_metrics(
    labels: Sequence[str], probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> dict[str, Any]:
    y, p = _rows(labels, probabilities, classes)
    n = len(y)
    nll = float(-np.log(np.maximum(p[np.arange(n), y], P_FLOOR)).mean())
    brier = float(np.mean(np.sum((p - np.eye(len(classes))[y]) ** 2, axis=1)))
    confidence, predicted = p.max(axis=1), p.argmax(axis=1)
    bins = []
    ece = 0.0
    for b, left in enumerate(ECE_EDGES[:-1]):
        right = ECE_EDGES[b + 1]
        mask = (confidence >= left) & ((confidence < right) if b < 9 else (confidence <= right))
        count = int(mask.sum())
        cs = float(confidence[mask].sum())
        correct = int((predicted[mask] == y[mask]).sum())
        bins.append({"bin": b + 1, "count": count, "confidence_sum": cs, "correct_sum": correct})
        if count:
            ece += count / n * abs(correct / count - cs / count)
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
    subject_records: Mapping[str, Mapping[str, Any]],
    metric: str,
    *,
    frozen_subjects: Sequence[str] | None = None,
    exclusion_manifest: Sequence[Mapping[str, Any]] | None = None,
    population_rule_id: str = "subject_macro_min_windows_1",
) -> dict[str, Any]:
    if metric not in ESTIMANDS or frozen_subjects is None or exclusion_manifest is None:
        raise V21Error("FROZEN_POPULATION_AND_EXCLUSION_MANIFEST_REQUIRED")
    subjects = tuple(frozen_subjects)
    exclusions = [dict(x) for x in exclusion_manifest]
    exclusion_ids = [str(x.get("subject_id")) for x in exclusions]
    if (
        len(set(subjects)) != len(subjects)
        or any(s not in subject_records for s in subjects)
        or len(set(exclusion_ids)) != len(exclusion_ids)
        or any(s not in subjects for s in exclusion_ids)
    ):
        raise V21Error("FROZEN_POPULATION_MISMATCH")
    if any(not x.get("reason") for x in exclusions):
        raise V21Error("INVALID_EXCLUSION_MANIFEST")
    excluded_ids = set(exclusion_ids)
    values = []
    for subject in subjects:
        if subject in excluded_ids:
            continue
        row = subject_records[subject]
        n = int(row.get("n", 0))
        value = row.get(metric)
        if n < 1 or value is None or not math.isfinite(float(value)):
            raise V21Error("SILENT_SUBJECT_EXCLUSION")
        values.append(float(value))
    if not values:
        return {
            "status": "NOT_ESTIMABLE",
            "reason": "NO_ELIGIBLE_SUBJECTS",
            "scope": "subject_macro",
            "population_rule_id": population_rule_id,
            "eligible_subjects": [],
            "excluded": exclusions,
        }
    return {
        "status": "ESTIMABLE",
        "value": float(np.mean(values)),
        "scope": "subject_macro",
        "population_rule_id": population_rule_id,
        "eligible_subjects": [s for s in subjects if s not in excluded_ids],
        "excluded": exclusions,
        "eligible_subject_hash": canonical_hash([s for s in subjects if s not in excluded_ids]),
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
        if support:
            observed.append(cls)
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
    values = [r["f1"]["value"] for r in rows if r["class"] in observed]
    bad = [r["class"] for r in rows if r["f1"]["status"] != "ESTIMABLE"]
    return {
        "classes": rows,
        "K_obs": observed,
        "observed_macro_f1": {
            "status": "ESTIMABLE",
            "value": float(np.mean(values)),
            "denominator": len(values),
        }
        if values
        else {"status": "NOT_ESTIMABLE", "reason": "NO_OBSERVED_CLASSES"},
        "fixed_vocabulary_macro_f1": {
            "status": "NOT_ESTIMABLE",
            "reason": "ZERO_F1_DENOMINATOR",
            "classes": bad,
        }
        if bad
        else {
            "status": "ESTIMABLE",
            "value": float(np.mean([r["f1"]["value"] for r in rows])),
            "denominator": len(classes),
        },
    }


def job_seed(job_id: str) -> tuple[str, int]:
    if not isinstance(job_id, str) or not job_id:
        raise V21Error("EMPTY_JOB_ID")
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return digest, int(digest, 16)


def _bootstrap_job(job_id: str) -> tuple[str, str, str, str]:
    parts = job_id.split("|")
    if parts[:2] != [PROTOCOL_VERSION, "bootstrap"] or not parts or parts[-1] != "seed=0":
        raise V21Error("INVALID_BOOTSTRAP_JOB_ID")
    if len(parts) == 8 and parts[2] == "subject_macro":
        _, _, kind, estimand, arm_kind, configuration, state, _ = parts
        if (
            estimand not in ESTIMANDS
            or arm_kind != "single_arm"
            or configuration not in {"full_sensor", "back_only", "thigh_only"}
            or state not in {"calibrated", "uncalibrated"}
        ):
            raise V21Error("INVALID_BOOTSTRAP_JOB_ID")
        return kind, estimand, arm_kind, f"{configuration}|{state}"
    if len(parts) == 10 and parts[2] == "paired_delta":
        _, _, kind, estimand, contrast_id, config_a, state_a, config_b, state_b, _ = parts
        if (
            estimand not in ESTIMANDS
            or not contrast_id
            or config_a not in {"full_sensor", "back_only", "thigh_only"}
            or config_b not in {"full_sensor", "back_only", "thigh_only"}
            or state_a not in {"calibrated", "uncalibrated"}
            or state_b not in {"calibrated", "uncalibrated"}
        ):
            raise V21Error("INVALID_BOOTSTRAP_JOB_ID")
        return kind, estimand, contrast_id, f"{config_a}|{state_a}|{config_b}|{state_b}"
    raise V21Error("INVALID_BOOTSTRAP_JOB_ID")


def frozen_quantile(values: Sequence[float], q: float) -> float:
    if not 0 <= q <= 1 or len(values) == 0:
        raise V21Error("INVALID_QUANTILE_INPUT")
    x = np.sort(np.asarray(values, dtype=float))
    h = (len(x) - 1) * q
    i = math.floor(h)
    j = math.ceil(h)
    return float(x[i] + (h - i) * (x[j] - x[i]))


def bootstrap(
    subject_values: Mapping[str, float], *, job_id: str, reps: int = BOOTSTRAP_REPS
) -> dict[str, Any]:
    kind, estimand, contrast, arm = _bootstrap_job(job_id)
    if (
        reps != BOOTSTRAP_REPS
        or not subject_values
        or any(not isinstance(k, str) or not k for k in subject_values)
    ):
        raise V21Error("INVALID_BOOTSTRAP_INPUT")
    digest, seed = job_seed(job_id)
    vals = np.asarray([_finite(v, "metric") for v in subject_values.values()], dtype=float)
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = vals[rng.integers(0, len(vals), size=(reps, len(vals)))].mean(axis=1)
    finite = draws[np.isfinite(draws)]
    out = {
        "job_id": job_id,
        "job_sha256": digest,
        "unsigned_seed": seed,
        "generator": "PCG64",
        "job_kind": kind,
        "estimand": estimand,
        "contrast_id": contrast,
        "arm_binding": arm,
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
    if estimand not in {"NLL", "BRIER", "ECE"} or not differences:
        raise V21Error("INVALID_PVALUE_INPUT")
    job_id = f"{PROTOCOL_VERSION}|pvalue|{estimand}|calibrated_vs_uncalibrated|seed=0"
    digest, seed = job_seed(job_id)
    d = np.asarray([_finite(v, "difference") for v in differences.values()], dtype=float)
    if len(d) < MIN_PAIRED_SUBJECTS:
        return {
            "job_id": job_id,
            "hash": digest,
            "seed": seed,
            "generator": "PCG64",
            "T_obs": None,
            "draws": PVALUE_DRAWS,
            "p": None,
            "tie": "inclusive_leq",
            "zero": "unchanged",
            "status": "NOT_ESTIMABLE",
            "eligible_count": len(d),
        }
    rng = np.random.Generator(np.random.PCG64(seed))
    signs = rng.choice(np.array([-1.0, 1.0]), size=(PVALUE_DRAWS, len(d)))
    observed = float(d.mean())
    count = int(np.sum((signs * d).mean(axis=1) <= observed))
    return {
        "job_id": job_id,
        "hash": digest,
        "seed": seed,
        "generator": "PCG64",
        "T_obs": observed,
        "draws": PVALUE_DRAWS,
        "p": (1 + count) / (PVALUE_DRAWS + 1),
        "tie": "inclusive_leq",
        "zero": "unchanged",
        "status": "ESTIMABLE",
        "eligible_count": len(d),
    }


def holm(pvalues: Mapping[str, float], *, alpha: float = 0.05) -> dict[str, Any]:
    if set(pvalues) != set(HOLM_IDS):
        return {"status": "INCOMPLETE_FAMILY", "hypotheses": [], "alpha": alpha, "m": 3}
    if (
        set(pvalues) != set(HOLM_IDS)
        or not math.isfinite(alpha)
        or not 0 < alpha < 1
        or any(not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in pvalues.values())
    ):
        raise V21Error("INVALID_HOLM_INPUT")
    order = sorted(HOLM_IDS, key=lambda x: (float(pvalues[x]), HOLM_IDS.index(x)))
    rows: list[dict[str, Any]] = []
    stop: int | None = None
    raw_values: list[float] = []
    groups: dict[float, int] = {}
    next_group = 0
    for ident in order:
        value = float(pvalues[ident])
        if value not in groups:
            next_group += 1
            groups[value] = next_group
    for rank, ident in enumerate(order, 1):
        p = float(pvalues[ident])
        threshold = alpha / (4 - rank)
        local = p <= threshold
        if stop is None and not local:
            stop = rank
        raw_values.append(min(1.0, (4 - rank) * p))
        rows.append(
            {
                "identifier": ident,
                "raw_p": p,
                "sorted_rank": rank,
                "equality_tie_group": groups[p],
                "threshold": threshold,
                "raw_holm": raw_values[-1],
                "local_pass": local,
            }
        )
    running = 0.0
    by: dict[str, float] = {}
    for row, raw in zip(rows, raw_values, strict=True):
        running = max(running, raw)
        by[str(row["identifier"])] = running
    for row in rows:
        row.update(
            {
                "adjusted_p": by[str(row["identifier"])],
                "final_reject": stop is None or int(row["sorted_rank"]) < stop,
                "stop_rank": stop,
            }
        )
    return {
        "status": "COMPLETE",
        "family_id": "v2-primary-calibrated-vs-uncalibrated-3",
        "alpha": alpha,
        "m": 3,
        "sort_rule": "ascending_raw_p",
        "tie_rule": list(HOLM_IDS),
        "hypotheses": rows,
    }


def pairing_manifest(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not records:
        raise V21Error("EMPTY_PAIRING_MANIFEST")
    required = {
        "subject_id",
        "contrast_id",
        "estimand_id",
        "reason",
        "arm",
        "window_set_hash",
        "population_rule_id",
    }
    ordered = [dict(r) for r in records]
    if any(set(row) != required for row in ordered):
        raise V21Error("PAIRING_FIELDS_MISMATCH")
    if any(
        not isinstance(row["subject_id"], str)
        or not row["subject_id"]
        or not re.fullmatch(r"[0-9a-f]{64}", row["window_set_hash"])
        or row["reason"]
        not in {"", "ZERO_WINDOWS", "STAGE_FAILURE", "WINDOW_ID_MISMATCH", "ESTIMAND_NOT_ESTIMABLE"}
        for row in ordered
    ):
        raise V21Error("INVALID_PAIRING_RECORD")
    keys = [(r["subject_id"], r["contrast_id"], r["estimand_id"], r["arm"]) for r in ordered]
    if len(set(keys)) != len(keys):
        raise V21Error("DUPLICATE_PAIRING_RECORD")
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in ordered:
        groups.setdefault((row["subject_id"], row["contrast_id"], row["estimand_id"]), []).append(
            row
        )
    for rows in groups.values():
        if {r["arm"] for r in rows} != {"calibrated", "uncalibrated"}:
            raise V21Error("UNPAIRED_ARMS")
        if (
            len({r["population_rule_id"] for r in rows}) != 1
            or len({r["window_set_hash"] for r in rows}) != 1
        ):
            raise V21Error("WINDOW_OR_POPULATION_MISMATCH")
    return {
        "records": ordered,
        "pairing_manifest_hash": canonical_hash(ordered),
        "eligible_subject_hash": canonical_hash(
            sorted({r["subject_id"] for r in ordered if not r["reason"]})
        ),
        "population_rule_id": ordered[0]["population_rule_id"],
    }


def window_set_hash(windows: Sequence[Sequence[Any]]) -> str:
    return canonical_hash(list(windows))


def transition(
    current: str,
    target: str,
    *,
    scope: str = "run",
    reason: str | None = None,
    required_fields_missing: Sequence[str] = (),
) -> dict[str, Any]:
    if current not in STATES or target not in STATES or scope not in SCOPES:
        raise V21Error("INVALID_STATE_SCOPE")
    normal = {a: b for a, b in zip(STATES[:9], STATES[1:10], strict=True)}
    if target in {"FAILED", "NOT_ESTIMABLE", "INCOMPLETE_FAMILY"}:
        if current in {"COMPLETE", "FAILED", "NOT_ESTIMABLE", "INCOMPLETE_FAMILY"} or not reason:
            raise V21Error("INVALID_TERMINAL_TRANSITION")
        return {
            "state": target,
            "scope": scope,
            "reason": reason,
            "required_fields_missing": list(required_fields_missing),
        }
    if normal.get(current) != target:
        raise V21Error(f"invalid transition {current}->{target}")
    return {"state": target, "scope": scope, "required_fields_missing": []}


def build_artifact_hashes(
    *,
    protocol: Any,
    schema: Any,
    config: Any,
    code: bytes,
    input_data: Any,
    vocabulary: Sequence[str],
    eligibility_manifest: Any | None = None,
    pairing: Any | None = None,
) -> dict[str, str]:
    return {
        "protocol_sha256": canonical_hash(protocol),
        "schema_sha256": canonical_hash(schema),
        "config_sha256": canonical_hash(config),
        "code_sha256": code if isinstance(code, str) else sha256_bytes(code),
        "input_sha256": canonical_hash(input_data),
        "vocabulary_sha256": canonical_hash(list(vocabulary)),
        "eligibility_manifest_sha256": canonical_hash(eligibility_manifest)
        if eligibility_manifest is not None
        else "",
        "pairing_manifest_sha256": canonical_hash(pairing) if pairing is not None else "",
    }


def _validate_json_schema_document(data: Mapping[str, Any]) -> None:
    schema_path = Path(__file__).resolve().parents[4] / "schemas" / "v2.1" / "Result.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(data)
    except JsonSchemaValidationError as exc:
        raise V21Error(f"JSON_SCHEMA_VALIDATION: {exc.message}") from exc


def validate_approval_provenance(value: Mapping[str, Any]) -> None:
    if (
        value.get("proposal_sha256") != APPROVED_PROPOSAL_SHA256
        or value.get("decision_sha256") != APPROVED_DECISION_SHA256
        or value.get("review_revision") != 4
        or value.get("allow_code_fix") is not True
        or value.get("allow_rerun") is not False
    ):
        raise V21Error("APPROVAL_PROVENANCE_MISMATCH")


def validate_result(
    data: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any] | None = None,
    _schema_checked: bool = False,
) -> dict[str, Any]:
    if not _schema_checked:
        _validate_json_schema_document(data)
    if (
        not isinstance(data, Mapping)
        or data.get("schema_version") != SCHEMA_VERSION
        or data.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise V21Error("SCHEMA_VERSION_MISMATCH")
    allowed = {
        "schema_version",
        "protocol_version",
        "status",
        "state",
        "scope",
        "provenance",
        "execution_authorization",
        "reason",
        "required_fields_missing",
        "hashes",
        "support",
        "estimability",
        "population",
        "pairing",
        "inference",
        "exploratory",
        "family",
        "outputs",
        "claim_boundary",
    }
    if set(data) - allowed:
        raise V21Error("UNKNOWN_RESULT_FIELD")
    if (
        data.get("status") not in {"COMPLETE", "FAILED", "NOT_ESTIMABLE", "INCOMPLETE_FAMILY"}
        or data.get("state") not in STATES
        or data.get("scope") not in SCOPES
    ):
        raise V21Error("INVALID_RESULT_STATE")
    # A deliberately incomplete terminal artifact has no inference payload;
    # it remains structurally valid while carrying NOT_ESTIMABLE truth.
    if data.get("status") == "NOT_ESTIMABLE" and "inference" not in data:
        return cast(dict[str, Any], json.loads(json.dumps(dict(data), ensure_ascii=False)))
    hashes = data.get("hashes")
    for section, expected_keys in {
        "support": {"training", "inner_calibration", "held_out_test"},
        "estimability": set(ESTIMANDS),
        "population": {
            "frozen_subject_ids",
            "exclusions",
            "population_rule_id",
            "eligibility_manifest_hash",
        },
        "pairing": {"pairing_manifest_hash", "eligible_subject_hash", "records"},
        "inference": {"single_arm", "paired", "ablations", "pvalues"},
        "family": {"family_id", "hypotheses", "alpha", "m", "status"},
        "outputs": {"generator", "manuscript"},
    }.items():
        value = data.get(section)
        if not isinstance(value, Mapping) or set(value) != expected_keys:
            if (
                section == "support"
                and isinstance(value, Mapping)
                and set(value) == expected_keys | {"folds"}
            ):
                continue
            raise V21Error(f"{section.upper()}_FIELD_MATRIX_MISMATCH")
    hashes = data.get("hashes")
    required = set(_HASHES)
    if not isinstance(hashes, Mapping) or set(hashes) != required:
        raise V21Error("HASH_FIELD_MATRIX_MISMATCH")
    for name in _HASHES:
        _hash(hashes[name], name)
    family = cast(Mapping[str, Any], data["family"])
    if (
        family["family_id"] != "v2-primary-calibrated-vs-uncalibrated-3"
        or family["m"] != 3
        or not isinstance(family["hypotheses"], list)
    ):
        raise V21Error("HOLM_FIELD_MATRIX_MISMATCH")
    hypothesis_fields = {
        "identifier",
        "raw_p",
        "sorted_rank",
        "equality_tie_group",
        "threshold",
        "raw_holm",
        "adjusted_p",
        "local_pass",
        "final_reject",
        "stop_rank",
    }
    for hypothesis in family["hypotheses"]:
        if not isinstance(hypothesis, Mapping) or set(hypothesis) != hypothesis_fields:
            raise V21Error("HOLM_HYPOTHESIS_FIELD_MATRIX_MISMATCH")
    inference = cast(Mapping[str, Any], data["inference"])
    pvalues = inference["pvalues"]
    if set(pvalues) != {f"H_{metric.upper()}" for metric in ESTIMANDS}:
        raise V21Error("PVALUE_FAMILY_FIELD_MATRIX_MISMATCH")
    pvalue_fields = {
        "job_id",
        "hash",
        "seed",
        "generator",
        "T_obs",
        "draws",
        "p",
        "tie",
        "zero",
        "status",
        "eligible_count",
    }
    for artifact in pvalues.values():
        if not isinstance(artifact, Mapping) or set(artifact) != pvalue_fields:
            raise V21Error("PVALUE_ARTIFACT_FIELD_MATRIX_MISMATCH")
        if artifact["hash"] != hashlib.sha256(str(artifact["job_id"]).encode("utf-8")).hexdigest():
            raise V21Error("PVALUE_JOB_HASH_MISMATCH")
        if artifact["status"] == "ESTIMABLE" and (
            artifact["p"] is None or artifact["T_obs"] is None
        ):
            raise V21Error("ESTIMABLE_PVALUE_MISSING_VALUE")
        if artifact["status"] == "NOT_ESTIMABLE" and artifact["p"] is not None:
            raise V21Error("NONESTIMABLE_PVALUE_HAS_VALUE")
    for hypothesis in family["hypotheses"]:
        artifact = pvalues.get(hypothesis["identifier"])
        if artifact is None or artifact["p"] != hypothesis["raw_p"]:
            raise V21Error("FAMILY_PVALUE_BINDING_MISMATCH")
    pairing = cast(Mapping[str, Any], data["pairing"])
    pairing_records = pairing["records"]
    if pairing["pairing_manifest_hash"] != canonical_hash(pairing_records):
        raise V21Error("PAIRING_MANIFEST_HASH_MISMATCH")
    for job_id in inference["ablations"]:
        parts = job_id.split("|")
        if len(parts) < 6 or parts[4] not in {
            "back_only_vs_full_sensor",
            "thigh_only_vs_full_sensor",
        }:
            raise V21Error("ABLATION_JOB_CONTRAST_MISMATCH")
        contrast, metric = parts[4], parts[3]
        matching = [
            r
            for r in pairing_records
            if r["contrast_id"] == contrast and r["estimand_id"] == metric
        ]
        if not matching or {r["arm"] for r in matching} != {"calibrated", "uncalibrated"}:
            raise V21Error("ABLATION_PAIRING_MISSING")
    exploratory = cast(Mapping[str, Any], data["exploratory"])
    for report in exploratory["f1"].values():
        for row in report["class_records"]:
            tp, fp, fn = row["TP"], row["FP"], row["FN"]
            if row["support"] != tp + fn:
                raise V21Error("F1_SUPPORT_MISMATCH")
            for key, denominator, numerator, reason in (
                ("precision", tp + fp, tp, "ZERO_PREDICTED_POSITIVES"),
                ("recall", tp + fn, tp, "ZERO_TRUE_SUPPORT"),
            ):
                cell = row[key]
                expected = (
                    {"status": "ESTIMABLE", "value": numerator / denominator}
                    if denominator
                    else {"status": "NOT_ESTIMABLE", "reason": reason}
                )
                if dict(cell) != expected:
                    raise V21Error("F1_COMPONENT_MISMATCH")
            denom = 2 * tp + fp + fn
            expected_f1 = (
                {"status": "ESTIMABLE", "value": 2 * tp / denom}
                if denom
                else {"status": "NOT_ESTIMABLE", "reason": "ZERO_F1_DENOMINATOR"}
            )
            if dict(row["f1"]) != expected_f1:
                raise V21Error("F1_VALUE_MISMATCH")
    support = cast(Mapping[str, Any], data["support"])
    held = cast(Mapping[str, Any], support["held_out_test"])
    for cls, count in held["counts"].items():
        status = held["class_status"].get(cls)
        if not isinstance(status, Mapping) or status["support"] != count:
            raise V21Error("AGGREGATE_SUPPORT_CONTRADICTION")
    if "folds" in support:
        for fold in support["folds"]:
            for cls in fold["held_out_test"]["zero_support"]:
                if fold["held_out_test"]["counts"].get(cls) != 0:
                    raise V21Error("FOLD_ZERO_SUPPORT_CONTRADICTION")
    if data.get("status") == "COMPLETE" and data.get("state") != "COMPLETE":
        raise V21Error("COMPLETE_STATE_MISMATCH")
    if data.get("status") != "FAILED" and data.get("required_fields_missing"):
        raise V21Error("MISSING_FIELDS_ON_NONFAILURE")
    if artifacts is not None:
        expected = build_artifact_hashes(
            protocol=artifacts["protocol"],
            schema=artifacts["schema"],
            config=artifacts["config"],
            code=artifacts["code"],
            input_data=artifacts["input"],
            vocabulary=artifacts["vocabulary"],
            eligibility_manifest=artifacts.get("eligibility_manifest"),
            pairing=artifacts.get("pairing"),
        )
        if dict(hashes) != expected:
            raise V21Error("ARTIFACT_HASH_BINDING_MISMATCH")
    return cast(dict[str, Any], json.loads(json.dumps(dict(data), ensure_ascii=False)))


def validate_json_schema(
    data: Mapping[str, Any], *, artifacts: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Canonical parity entry point: Python and JSON-schema callers share this validator."""
    _validate_json_schema_document(data)
    return validate_result(data, artifacts=artifacts, _schema_checked=True)


validate_schema_parity = validate_json_schema


def migration_v2_to_v21(
    source: Mapping[str, Any], *, source_hash: str | None = None
) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        raise V21Error("INVALID_MIGRATION_SOURCE")
    forbidden = {
        "metrics",
        "predictions",
        "probabilities",
        "losses",
        "nll",
        "brier",
        "ece",
        "accuracy",
        "macro_f1",
    }
    found: list[str] = []

    def scan(value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if str(key).lower() in forbidden:
                    found.append(next_path)
                scan(child, next_path)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                scan(child, f"{path}[{i}]")

    scan(source)
    report = {
        "report_version": "migration-report-v2-to-v2.1",
        "source_schema_version": source.get("schema_version"),
        "source_hash": source_hash or canonical_hash(source),
        "target_schema_version": SCHEMA_VERSION,
        "target_hash": None,
        "status": "REJECTED" if found or source.get("schema_version") else "UNAVAILABLE",
        "field_mapping": [
            {
                "field": path,
                "source_present": True,
                "target_required": True,
                "action": "reject",
                "reason": "metric-bearing-field-refusal",
            }
            for path in found
        ]
        or [
            {
                "field": "v2.1.required_support_estimand_manifests",
                "source_present": False,
                "target_required": True,
                "action": "unavailable",
                "reason": "cannot invent structural fields or relabel v2",
            }
        ],
        "metric_fields_refused": found,
    }
    return report


def generate_outputs(result: Mapping[str, Any], *, timeout_check: Any = None) -> dict[str, str]:
    """Render only from the already-validated in-memory result contract.

    The result's claim boundary is authoritative; this function never invents a
    performance claim and is called only after artifact-bound validation.
    """
    if timeout_check is not None:
        timeout_check()
    status = str(result.get("status", "UNKNOWN"))
    boundary = str(result.get("claim_boundary", "UNVERIFIED"))
    real = boundary == "guarded_real_quarantined_no_release"
    payload = {
        "status": status,
        "claim_boundary": boundary,
        "scientific_status": "UNVERIFIED",
        "real_data": real,
        "performance_bearing": real,
        "release": False,
        "source_result_hash": canonical_hash(result),
    }
    rendered = {
        "generator.json": canonical_bytes(payload).decode("utf-8"),
        "manuscript.md": (
            f"HARTH protocol-v2.1 status: {status}. No performance claim. "
            "Scientific status: UNVERIFIED; release: false.\n"
        ),
    }
    if timeout_check is not None:
        timeout_check()
    return rendered


def render_generator_output(result: Mapping[str, Any]) -> str:
    return generate_outputs(result)["generator.json"]


def render_manuscript_output(result: Mapping[str, Any]) -> str:
    return generate_outputs(result)["manuscript.md"]


def atomic_canonical_write(path: str | Path, value: Any) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value)
    digest = sha256_bytes(data)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError as exc:
            raise V21Error("IMMUTABLE_OUTPUT_EXISTS") from exc
        os.unlink(tmp)
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return digest
