# ruff: noqa: E501
"""Strict, versioned handoff contract from HARTH engine artifacts to paper tooling."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any, cast

SCHEMA_VERSION = "harth-result-v1"
CONFIGURATIONS = ("full_sensor", "back_only", "thigh_only")
STATES = ("uncalibrated", "calibrated")
METRICS = ("nll", "brier", "ece", "accuracy", "macro_f1")


class ResultSchemaError(ValueError):
    """A result artifact is not safe for manuscript generation."""


def _fail(message: str) -> None:
    raise ResultSchemaError(message)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"missing or invalid {name}")
    return cast(str, value)


def _hash(value: Any, name: str) -> str:
    value = _string(value, name)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        _fail(f"invalid {name}")
    return cast(str, value)


def _number(value: Any, name: str, *, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        _fail(f"invalid finite {name}")
    result = float(value)
    if lo is not None and result < lo or hi is not None and result > hi:
        _fail(f"out-of-range {name}")
    return result


def _interval(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(f"malformed {name} interval")
    low = _number(value[0], f"{name} lower")
    high = _number(value[1], f"{name} upper")
    if low >= high:
        _fail(f"malformed {name} interval")
    return [low, high]


def _metrics(value: Any, name: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        _fail(f"missing {name} metrics")
    result: dict[str, float] = {}
    for metric in METRICS:
        if metric in value:
            upper = 1.0 if metric in {"brier", "ece", "accuracy", "macro_f1"} else None
            result[metric] = _number(value[metric], f"{name}.{metric}", lo=0.0, hi=upper)
    if set(result) != set(METRICS):
        _fail(f"incomplete {name} metrics")
    return result


def validate_result(data: object) -> dict[str, Any]:
    """Validate and normalize an engine-to-paper artifact, failing closed."""
    if not isinstance(data, Mapping):
        _fail("result must be an object")
    data = dict(cast(Mapping[str, Any], data))
    if data.get("schema_version") != SCHEMA_VERSION or data.get("status") != "VALIDATED":
        _fail("unsupported or unvalidated result schema")
    protocol_id = _string(data.get("protocol_id"), "protocol_id")
    input_hash = _hash(data.get("input_hash"), "input_hash")
    protocol_hash = _hash(data.get("protocol_hash"), "protocol_hash")
    code_hash = _hash(data.get("code_hash"), "code_hash")
    provenance = data.get("provenance")
    if not isinstance(provenance, Mapping):
        _fail("missing provenance")
    provenance = dict(cast(Mapping[str, Any], provenance))
    for key, expected in (
        ("input_hash", input_hash),
        ("protocol_hash", protocol_hash),
        ("code_hash", code_hash),
    ):
        if provenance.get(key) != expected:
            _fail(f"provenance {key} mismatch")
    classes = data.get("class_vocabulary")
    if (
        not isinstance(classes, list)
        or not classes
        or any(not isinstance(x, str) or not x for x in classes)
    ):
        _fail("invalid class vocabulary")
    classes = cast(list[str], classes)
    if len(set(classes)) != len(classes):
        _fail("duplicate class vocabulary")
    fold_ids = data.get("fold_ids")
    folds = data.get("folds")
    if (
        not isinstance(fold_ids, list)
        or not fold_ids
        or any(not isinstance(x, str) or not x for x in fold_ids)
    ):
        _fail("missing canonical fold IDs")
    fold_ids = cast(list[str], fold_ids)
    if (
        len(set(fold_ids)) != len(fold_ids)
        or not isinstance(folds, list)
        or len(folds) != len(fold_ids)
    ):
        _fail("duplicate or missing folds")
    folds = cast(list[Mapping[str, Any]], folds)
    normalized_folds: list[dict[str, Any]] = []
    for fold in folds:
        if not isinstance(fold, Mapping):
            _fail("malformed fold")
        fold = dict(fold)
        fold_id = _string(fold.get("fold_id"), "fold_id")
        if fold_id not in fold_ids or any(
            row.get("fold_id") == fold_id for row in normalized_folds
        ):
            _fail("duplicate or unknown fold ID")
        configuration = fold.get("configuration")
        if configuration not in CONFIGURATIONS:
            _fail("invalid configuration")
        subject = _string(fold.get("test_subject"), "test_subject")
        train = fold.get("train_subjects")
        if (
            not isinstance(train, list)
            or not train
            or any(not isinstance(x, str) or not x for x in train)
        ):
            _fail("invalid train subjects")
        train = cast(list[str], train)
        temperature = _number(fold.get("temperature"), "temperature", lo=0.05, hi=20.0)
        optimizer = fold.get("optimizer")
        if not isinstance(optimizer, Mapping) or not isinstance(optimizer.get("converged"), bool):
            _fail("missing optimizer convergence")
        optimizer = dict(cast(Mapping[str, Any], optimizer))
        states = fold.get("states")
        if not isinstance(states, Mapping) or set(states) != set(STATES):
            _fail("missing calibration states")
        states = dict(cast(Mapping[str, Any], states))
        normalized_states = {}
        for state in STATES:
            row = states[state]
            if not isinstance(row, Mapping):
                _fail("malformed calibration state")
            row = dict(cast(Mapping[str, Any], row))
            metrics = _metrics(row.get("metrics"), f"{fold_id}.{state}")
            interval = _interval(row.get("interval"), f"{fold_id}.{state}")
            support = row.get("class_support")
            if (
                not isinstance(support, Mapping)
                or set(support) != set(classes)
                or any(
                    isinstance(v, bool) or not isinstance(v, int) or v < 2 for v in support.values()
                )
            ):
                _fail("invalid class support")
            support = cast(Mapping[str, int], support)
            normalized_states[state] = {
                "metrics": metrics,
                "interval": interval,
                "class_support": dict(support),
            }
        normalized_folds.append(
            {
                "fold_id": fold_id,
                "configuration": configuration,
                "test_subject": subject,
                "train_subjects": list(train),
                "temperature": temperature,
                "optimizer": {"converged": optimizer["converged"]},
                "states": normalized_states,
                "failures": list(fold.get("failures", [])),
            }
        )
    if {x["fold_id"] for x in normalized_folds} != set(fold_ids):
        _fail("missing or extra fold IDs")
    gates = data.get("gates")
    derived = {
        "provenance": True,
        "finite_metrics": True,
        "fold_integrity": True,
        "class_support": True,
    }
    if gates is not None and gates != derived:
        _fail("caller-supplied gates do not match validated gates")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "VALIDATED",
        "protocol_id": protocol_id,
        "input_hash": input_hash,
        "protocol_hash": protocol_hash,
        "code_hash": code_hash,
        "class_vocabulary": list(classes),
        "provenance": dict(provenance),
        "fold_ids": list(fold_ids),
        "folds": normalized_folds,
        "gates": derived,
        "failures": list(data.get("failures", [])),
        "analysis": data.get("analysis"),
    }


def engine_result_to_schema(result: Any, *, code_hash: str) -> dict[str, Any]:
    """Translate EngineResult; this is the only engine/paper boundary."""
    raw_folds = []
    for row in result.folds:
        states = {}
        for state in STATES:
            source = row[state][row["test_subject"]]
            metrics = {key: source[key] for key in METRICS}
            metrics.setdefault("accuracy", source.get("accuracy", 0.0))
            if not isinstance(source.get("interval"), list) or len(source["interval"]) < 6:
                raise ResultSchemaError("engine did not provide uncertainty")
            if not isinstance(source.get("class_support"), Mapping):
                raise ResultSchemaError("engine did not provide class support")
            if any(
                int(source["class_support"].get(str(i), 0)) < 1
                for i in range(len(result.class_vocabulary))
            ):
                raise ResultSchemaError("engine class support is incomplete")
            metrics.setdefault("macro_f1", source.get("macro_f1", 0.0))
            states[state] = {
                "metrics": metrics,
                "interval": [float(source["interval"][0]), float(source["interval"][1])],
                "class_support": {
                    c: int(source["class_support"].get(str(i), 0))
                    for i, c in enumerate(result.class_vocabulary)
                },
            }
        raw_folds.append(
            {
                "fold_id": f"{row['configuration']}::{row['test_subject']}",
                "configuration": row["configuration"],
                "test_subject": row["test_subject"],
                "train_subjects": row["train_subjects"],
                "temperature": row["selected_temperature"],
                "optimizer": {"converged": row["optimizer_converged"]},
                "states": states,
            }
        )
    from .analysis import summarize_fold_rows

    return validate_result(
        {
            "schema_version": SCHEMA_VERSION,
            "status": "VALIDATED",
            "protocol_id": result.protocol_id,
            "input_hash": result.input_hash,
            "protocol_hash": result.protocol_hash,
            "code_hash": code_hash,
            "class_vocabulary": list(result.class_vocabulary),
            "provenance": {
                "input_hash": result.input_hash,
                "protocol_hash": result.protocol_hash,
                "code_hash": code_hash,
                "source": "engine",
            },
            "fold_ids": [x["fold_id"] for x in raw_folds],
            "folds": raw_folds,
            "analysis": summarize_fold_rows(result.folds),
        }
    )


def code_digest(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()
