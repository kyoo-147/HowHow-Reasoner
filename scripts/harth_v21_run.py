"""Guarded HARTH protocol-v2.1 runner.

Synthetic fixtures are explicit and separate from ``--execute-real``.  Real mode
requires an independently-created, one-shot authorization record and never resumes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from howhow.episodes.harth.v2 import RunGuard, load_harth_archive, run_protocol
from howhow.episodes.harth.v2.engine import input_hash
from howhow.episodes.harth.v21 import (
    APPROVED_DECISION_SHA256,
    APPROVED_PROPOSAL_SHA256,
    ESTIMANDS,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    V21Error,
    atomic_canonical_write,
    bootstrap,
    build_artifact_hashes,
    canonical_bytes,
    canonical_hash,
    generate_outputs,
    holm,
    pairing_manifest,
    pvalue,
    support_gate,
    validate_approval_provenance,
    validate_result,
    window_set_hash,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "episodes/harth-calibration/protocol/protocol-v2.1.json"
CONFIG = ROOT / "episodes/harth-calibration/run-config-v2.1.json"
SCHEMA = ROOT / "schemas/v2.1/Result.json"
CLASSES = ["1", "2", "3", "4", "5", "6", "7", "8", "13", "14", "130", "140"]
REAL_CONSENT_ENV = "HOWHOW_RUN_REAL_HARTH_V21"
BUDGETS = {"timeout_seconds": 1800, "bootstrap_reps": 2000, "pvalue_draws": 200000}
AUTH_FIELDS = {
    "authorization_version",
    "protocol_version",
    "decision_id",
    "decision_sha256",
    "allow_rerun",
    "allow_resume",
    "allow_retry",
    "allow_tuning",
    "one_shot",
    "hashes",
    "budgets",
    "destination",
    "git_revision",
    "vocabulary",
}
PACKAGE_PATHS = {
    "main": "scripts/harth_v21_run.py",
    "protocol": "episodes/harth-calibration/protocol/protocol-v2.1.json",
    "config": "episodes/harth-calibration/run-config-v2.1.json",
    "schema": "schemas/v2.1/Result.json",
}
CODE_PATHS = (
    "scripts/harth_v21_run.py",
    "scripts/harth_v2_run.py",
    "src/howhow/episodes/harth/v21.py",
    "src/howhow/episodes/harth/v2/analysis.py",
    "src/howhow/episodes/harth/v2/engine.py",
    "src/howhow/episodes/harth/v2/loader.py",
    "src/howhow/episodes/harth/v2/result_schema.py",
    "src/howhow/episodes/harth/v2/run_guard.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob(path: str) -> str:
    """Return the index/HEAD Git blob identity, independent of checkout EOLs."""
    line = subprocess.run(
        ("git", "ls-files", "-s", "--", path),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if not line:
        raise V21Error(f"UNTRACKED_PACKAGE:{path}")
    return line.split()[1]


def _code_identity() -> tuple[str, str]:
    status = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=no"),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if status:
        raise V21Error("DIRTY_TRACKED_SOURCE_REJECTED")
    files = subprocess.run(
        ("git", "ls-files", "--", *CODE_PATHS), cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.splitlines()
    if set(files) != set(CODE_PATHS):
        raise V21Error("CODE_IDENTITY_MANIFEST_MISMATCH")
    blobs = subprocess.run(
        ("git", "ls-files", "-s", "--", *CODE_PATHS),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    entries = sorted((line.split(maxsplit=3)[3], line.split()[1]) for line in blobs)
    payload = canonical_bytes({"files": [{"path": p, "blob": b} for p, b in entries]})
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()
    return hashlib.sha256(payload).hexdigest(), revision


def _artifacts(
    archive: Path,
    code_hash: str,
    *,
    eligibility: Any = None,
    pairing: Any = None,
    input_data: Any = None,
) -> dict[str, Any]:
    return {
        "protocol": json.loads(PROTOCOL.read_text()),
        "schema": json.loads(SCHEMA.read_text()),
        "config": json.loads(CONFIG.read_text()),
        "code": code_hash,
        "input": input_data if input_data is not None else {"archive_sha256": _sha(archive)},
        "vocabulary": CLASSES,
        "eligibility_manifest": eligibility,
        "pairing": pairing,
    }


def _load_decision(path: Path, *, authorization: Mapping[str, Any]) -> dict[str, Any]:
    """Load the independently rerun decision and bind its exact bytes to auth."""
    try:
        raw = path.read_bytes()
        decision = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V21Error("DECISION_FILE_INVALID") from exc
    if (
        not isinstance(decision, dict)
        or hashlib.sha256(raw).hexdigest() != authorization["decision_sha256"]
    ):
        raise V21Error("DECISION_HASH_MISMATCH")
    required = {
        "decision_id",
        "protocol_version",
        "allow_rerun",
        "allow_resume",
        "allow_retry",
        "allow_tuning",
        "one_shot",
        "hashes",
        "budgets",
        "destination",
        "git_revision",
        "vocabulary",
    }
    if set(decision) != required:
        raise V21Error("DECISION_FIELDS_MISMATCH")
    expected = {key: authorization[key] for key in required if key != "hashes"}
    if any(decision[key] != value for key, value in expected.items()):
        raise V21Error("DECISION_AUTHORIZATION_MISMATCH")
    if decision["hashes"] != authorization["hashes"]:
        raise V21Error("DECISION_PACKAGE_HASH_MISMATCH")
    return decision


def _load_authorization(path: Path, *, archive: Path, output: Path) -> dict[str, Any]:
    try:
        auth = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V21Error("AUTHORIZATION_RECORD_INVALID") from exc
    if not isinstance(auth, dict):
        raise V21Error("AUTHORIZATION_RECORD_INVALID")
    # Legacy records are rejected as stale without becoming an executable authorization.
    if "decision_id" not in auth and isinstance(auth.get("hashes"), dict):
        if auth.get("git_revision") != _code_identity()[1]:
            raise V21Error("STALE_OR_WRONG_AUTHORIZATION")
    auth_schema = json.loads((ROOT / "schemas/v2.1/Authorization.json").read_text(encoding="utf-8"))
    try:
        Draft202012Validator(auth_schema).validate(auth)
    except Exception as exc:
        raise V21Error("AUTHORIZATION_SCHEMA_INVALID") from exc
    # Exact top-level schema: an authorization is not a bag of caller-controlled flags.
    if set(auth) != AUTH_FIELDS or auth.get("authorization_version") != "v2.1":
        # Preserve a useful stale error for legacy records, while never accepting them.
        if isinstance(auth.get("hashes"), dict) and auth.get("git_revision") != _code_identity()[1]:
            raise V21Error("STALE_OR_WRONG_AUTHORIZATION")
        raise V21Error("AUTHORIZATION_RECORD_FIELDS_MISMATCH")
    if auth["protocol_version"] != PROTOCOL_VERSION:
        raise V21Error("AUTHORIZATION_PROTOCOL_MISMATCH")
    if (
        auth["allow_rerun"] is not True
        or auth["allow_resume"] is not False
        or auth["allow_retry"] is not False
        or auth["allow_tuning"] is not False
        or auth["one_shot"] is not True
    ):
        raise V21Error("RERUN_NOT_AUTHORIZED")
    code_hash, revision = _code_identity()
    expected = {
        "main": _git_blob(PACKAGE_PATHS["main"]),
        "code": code_hash,
        "protocol": _git_blob(PACKAGE_PATHS["protocol"]),
        "config": _git_blob(PACKAGE_PATHS["config"]),
        "schema": _git_blob(PACKAGE_PATHS["schema"]),
        "archive": _sha(archive),
        "vocabulary": canonical_hash(CLASSES),
        "budgets": canonical_hash(BUDGETS),
    }
    if auth["hashes"] != expected or auth["git_revision"] != revision:
        raise V21Error("STALE_OR_WRONG_AUTHORIZATION")
    if not auth["decision_sha256"]:
        raise V21Error("DECISION_BINDING_MISMATCH")
    if auth["vocabulary"] != CLASSES or auth["destination"] != str(output.resolve()):
        raise V21Error("AUTHORIZATION_BINDING_MISMATCH")
    if auth["budgets"] != BUDGETS:
        raise V21Error("AUTHORIZATION_BUDGET_MISMATCH")
    validate_approval_provenance(
        {
            "proposal_sha256": APPROVED_PROPOSAL_SHA256,
            "decision_sha256": APPROVED_DECISION_SHA256,
            "review_revision": 4,
            "allow_code_fix": True,
            "allow_rerun": False,
        }
    )
    return auth


def _metric_value(stats: Mapping[str, Any], metric: str) -> float:
    n = int(stats["n"])
    if n < 1:
        raise V21Error("ZERO_WINDOWS")
    if metric == "nll":
        return float(stats["nll_sum"]) / n
    if metric == "brier":
        return float(stats["brier_sum"]) / n
    if metric == "ece":
        return float(
            sum(
                abs(row["correct_sum"] / row["count"] - row["confidence_sum"] / row["count"])
                * row["count"]
                / n
                for row in stats["ece_bins"]
                if row["count"]
            )
        )
    raise V21Error("UNKNOWN_PRIMARY_METRIC")


def _inference_report(
    engine: Any, subjects: list[str], timeout_check: Any = None
) -> dict[str, Any]:
    """Serialize every preregistered arm, contrast, and ablation bootstrap job."""
    rows = engine.folds
    report: dict[str, Any] = {"single_arm": {}, "paired": {}, "ablations": {}, "pvalues": {}}
    configurations = ("full_sensor", "back_only", "thigh_only")
    for configuration in configurations:
        if timeout_check is not None:
            timeout_check()
        selected = [row for row in rows if row["configuration"] == configuration]
        for state in ("calibrated", "uncalibrated"):
            for metric in ESTIMANDS:
                values = {
                    row["test_subject"]: _metric_value(
                        row["sufficient_statistics"][state][row["test_subject"]], metric
                    )
                    for row in selected
                }
                job = (
                    f"{PROTOCOL_VERSION}|bootstrap|subject_macro|{metric}|single_arm|"
                    f"{configuration}|{state}|seed=0"
                )
                report["single_arm"][job] = bootstrap(values, job_id=job)
        for metric in ESTIMANDS:
            differences = {
                row["test_subject"]: _metric_value(
                    row["sufficient_statistics"]["calibrated"][row["test_subject"]], metric
                )
                - _metric_value(
                    row["sufficient_statistics"]["uncalibrated"][row["test_subject"]], metric
                )
                for row in selected
            }
            job = (
                f"{PROTOCOL_VERSION}|bootstrap|paired_delta|{metric}|"
                f"calibrated_vs_uncalibrated|{configuration}|calibrated|"
                f"{configuration}|uncalibrated|seed=0"
            )
            report["paired"][job] = bootstrap(differences, job_id=job)
    for configuration in ("back_only", "thigh_only"):
        if timeout_check is not None:
            timeout_check()
        for state in ("calibrated", "uncalibrated"):
            for metric in ESTIMANDS:
                differences = {}
                for subject in subjects:
                    by_config = {
                        row["configuration"]: row for row in rows if row["test_subject"] == subject
                    }
                    differences[subject] = _metric_value(
                        by_config[configuration]["sufficient_statistics"][state][subject], metric
                    ) - _metric_value(
                        by_config["full_sensor"]["sufficient_statistics"][state][subject], metric
                    )
                job = (
                    f"{PROTOCOL_VERSION}|bootstrap|paired_delta|{metric}|"
                    f"{configuration}_vs_full_sensor|{configuration}|{state}|"
                    f"full_sensor|{state}|seed=0"
                )
                report["ablations"][job] = bootstrap(differences, job_id=job)
    return report


def _exact_f1_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Derive and serialize precision/recall/F1 from confusion counts."""
    tp, fp, fn = (int(row[key]) for key in ("TP", "FP", "FN"))
    support = tp + fn
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
    denominator = 2 * tp + fp + fn
    f1 = (
        {"status": "ESTIMABLE", "value": 2 * tp / denominator}
        if denominator
        else {"status": "NOT_ESTIMABLE", "reason": "ZERO_F1_DENOMINATOR"}
    )
    return {**dict(row), "support": support, "precision": precision, "recall": recall, "f1": f1}


def _build_result(
    engine: Any,
    windows: tuple[Any, ...],
    manifest: dict[str, Any],
    code_hash: str,
    archive: Path,
    *,
    claim: str = "synthetic_structural_only_no_performance_claim",
    forced_status: str | None = None,
    execution_authorization: Mapping[str, Any] | None = None,
    real_data: bool = False,
    timeout_check: Any = None,
) -> dict[str, Any]:
    folds = [f for f in engine.folds if f["configuration"] == "full_sensor"]
    subjects = sorted({w.subject for w in windows})
    if len(subjects) != 22:
        raise V21Error("FROZEN_POPULATION_MUST_BE_22")
    fold_support = []
    for fold in folds:
        test_subject = fold["test_subject"]
        train_labels = [w.label for w in windows if w.subject in fold["train_subjects"]]
        inner_rows = []
        for inner_subject in fold["train_subjects"]:
            inner_labels = [
                w.label
                for w in windows
                if w.subject in fold["train_subjects"] and w.subject != inner_subject
            ]
            inner_rows.append(
                {
                    "held_subject": inner_subject,
                    "support": support_gate(
                        inner_labels, CLASSES, stage="inner_calibration", minimum=2
                    ),
                }
            )
        fold_support.append(
            {
                "test_subject": test_subject,
                "training": support_gate(train_labels, CLASSES, stage="training", minimum=2),
                "inner_calibration": inner_rows,
                "held_out_test": support_gate(
                    [w.label for w in windows if w.subject == test_subject],
                    CLASSES,
                    stage="held_out_test",
                    minimum=2,
                ),
            }
        )
    held_support = support_gate(
        [w.label for w in windows], CLASSES, stage="held_out_test", minimum=2
    )
    # Aggregate held-out support is population-only.  Fold-local zero support
    # remains under support.folds and must not rewrite aggregate counts/status.
    # Held-out support is descriptive: macro NLL/Brier/ECE remain estimable.
    # Only class-specific exploratory cells carry NOT_ESTIMABLE state.
    values: dict[str, dict[str, dict[str, float]]] = {
        m: {s: {} for s in ("calibrated", "uncalibrated")} for m in ESTIMANDS
    }
    records: list[dict[str, Any]] = []
    if len(engine.folds) != 66 or {f["configuration"] for f in engine.folds} != {
        "full_sensor",
        "back_only",
        "thigh_only",
    }:
        raise V21Error("ENGINE_FOLD_CONFIGURATION_INCOMPLETE")
    for fold in folds:
        if timeout_check is not None:
            timeout_check()
        subject = fold["test_subject"]
        for arm in ("calibrated", "uncalibrated"):
            stats = fold["sufficient_statistics"][arm][subject]
            n = stats["n"]
            ece = sum(
                abs(row["correct_sum"] / row["count"] - row["confidence_sum"] / row["count"])
                * row["count"]
                / n
                for row in stats["ece_bins"]
                if row["count"]
            )
            values["nll"][arm][subject] = stats["nll_sum"] / n
            values["brier"][arm][subject] = stats["brier_sum"] / n
            values["ece"][arm][subject] = ece
            wh = window_set_hash([[w.provenance] for w in windows if w.subject == subject])
            for metric in ESTIMANDS:
                records.append(
                    {
                        "subject_id": subject,
                        "contrast_id": "calibrated_vs_uncalibrated",
                        "estimand_id": metric,
                        "reason": "",
                        "arm": arm,
                        "window_set_hash": wh,
                        "population_rule_id": "subject_macro_min_windows_1",
                    }
                )
    # Bind every ablation contrast/state used by bootstrap to the same frozen
    # subject/window population.  Each metric gets its own pairing records.
    for configuration in ("back_only", "thigh_only"):
        for state in ("calibrated", "uncalibrated"):
            contrast = f"{configuration}_vs_full_sensor"
            for subject in subjects:
                wh = window_set_hash([[w.provenance] for w in windows if w.subject == subject])
                for metric in ESTIMANDS:
                    records.append(
                        {
                            "subject_id": subject,
                            "contrast_id": contrast,
                            "estimand_id": metric,
                            "reason": "",
                            "arm": state,
                            "window_set_hash": wh,
                            "population_rule_id": "subject_macro_min_windows_1",
                        }
                    )
    pairing = pairing_manifest(records)
    raw = {
        "H_" + m.upper(): pvalue(
            {s: values[m]["calibrated"][s] - values[m]["uncalibrated"][s] for s in subjects},
            estimand=m.upper(),
        )
        for m in ESTIMANDS
    }
    inference = _inference_report(engine, subjects, timeout_check=timeout_check)
    # Keep the exact sign-flip artifacts in the validated inference section;
    # they are the source for family correction, never an ephemeral intermediate.
    inference["pvalues"] = raw
    raw_p = {k: row["p"] for k, row in raw.items() if row["status"] == "ESTIMABLE"}
    family = (
        holm(raw_p)
        if len(raw_p) == 3
        else {"status": "INCOMPLETE_FAMILY", "hypotheses": [], "alpha": 0.05, "m": 3}
    )
    eligibility = {"subjects": subjects, "rule": "subject_macro_min_windows_1"}
    hashes = build_artifact_hashes(
        protocol=json.loads(PROTOCOL.read_text()),
        schema=json.loads(SCHEMA.read_text()),
        config=json.loads(CONFIG.read_text()),
        code=code_hash,
        input_data=manifest,
        vocabulary=CLASSES,
        eligibility_manifest=eligibility,
        pairing=pairing,
    )
    status = forced_status or ("COMPLETE" if len(raw_p) == 3 else "INCOMPLETE_FAMILY")
    result = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": status,
        "state": status,
        "scope": "run",
        "provenance": {
            "proposal_sha256": APPROVED_PROPOSAL_SHA256,
            "decision_sha256": APPROVED_DECISION_SHA256,
            "review_revision": 4,
            "allow_code_fix": True,
            "allow_rerun": False,
        },
        **(
            {
                "execution_authorization": {
                    "decision_id": execution_authorization["decision_id"],
                    "decision_sha256": execution_authorization["decision_sha256"],
                    "destination": execution_authorization["destination"],
                    "one_shot": True,
                }
            }
            if execution_authorization is not None
            else {}
        ),
        "hashes": hashes,
        "inference": inference,
        "exploratory": {
            "frozen_population": {"subjects": subjects, "count": len(subjects)},
            "configuration_state_pairings": [
                {"configuration": configuration, "state": state, "subjects": subjects}
                for configuration in ("full_sensor", "back_only", "thigh_only")
                for state in ("calibrated", "uncalibrated")
            ],
            "f1": {
                f"{fold['configuration']}/{state}/{fold['test_subject']}": {
                    "class_records": [
                        _exact_f1_record(row)
                        for row in fold["sufficient_statistics"][state][fold["test_subject"]][
                            "classification"
                        ]
                    ],
                    "class_status": {
                        row["class"]: (
                            {"status": "OBSERVED", "support": row["support"]}
                            if row["support"]
                            else {"status": "NOT_ESTIMABLE", "support": 0, "reason": "ZERO_SUPPORT"}
                        )
                        for row in fold["sufficient_statistics"][state][fold["test_subject"]][
                            "classification"
                        ]
                    },
                }
                for fold in engine.folds
                for state in ("calibrated", "uncalibrated")
            },
        },
        "support": {
            "training": fold_support[0]["training"],
            "inner_calibration": fold_support[0]["inner_calibration"][0]["support"],
            "held_out_test": held_support,
            "folds": fold_support,
        },
        "estimability": {
            m: (
                {
                    "status": "ESTIMABLE",
                    "reason": "SUFFICIENT_ELIGIBLE_SUBJECTS",
                    "value": sum(values[m]["calibrated"].values()) / len(subjects),
                }
                if m in [k.lower().replace("h_", "") for k in raw_p]
                else {"status": "NOT_ESTIMABLE", "reason": "INSUFFICIENT_PAIRED_SUBJECTS"}
            )
            for m in ESTIMANDS
        },
        "population": {
            "frozen_subject_ids": subjects,
            "exclusions": [],
            "population_rule_id": eligibility["rule"],
            "eligibility_manifest_hash": canonical_hash(eligibility),
        },
        "pairing": {
            "pairing_manifest_hash": pairing["pairing_manifest_hash"],
            "eligible_subject_hash": pairing["eligible_subject_hash"],
            "records": records,
        },
        "family": {
            "family_id": "v2-primary-calibrated-vs-uncalibrated-3",
            "hypotheses": family["hypotheses"],
            "alpha": 0.05,
            "m": 3,
            "status": family["status"],
        },
        "outputs": {"generator": "", "manuscript": ""},
        "claim_boundary": claim,
    }
    artifacts = _artifacts(
        archive, code_hash, eligibility=eligibility, pairing=pairing, input_data=manifest
    )
    if timeout_check is not None:
        timeout_check()
    # Validate the exact quarantined artifact before deriving any publication text.
    validate_result(result, artifacts=artifacts)
    if timeout_check is not None:
        timeout_check()
    generated = generate_outputs(result, timeout_check=timeout_check)
    if timeout_check is not None:
        timeout_check()
    result["outputs"] = {
        "generator": generated["generator.json"],
        "manuscript": generated["manuscript.md"],
    }
    validate_result(result, artifacts=artifacts)
    return result


def _fixture_archive(path: Path, *, subjects: int = 22, missing: str | None = None) -> None:
    header = [
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
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for number in range(1, subjects + 1):
            rows: list[list[Any]] = [header]
            tick = datetime(2024, 1, 1, tzinfo=UTC)
            for ci, label in enumerate(CLASSES):
                if label == missing and number == 1:
                    continue
                for i in range(128):
                    value = float(ci * 10 + number / 100 + i / 10000)
                    rows.append(
                        [
                            (tick + timedelta(seconds=len(rows))).isoformat(),
                            f"S{number:03d}",
                            "synthetic",
                            label,
                            value,
                            value + 1,
                            value + 2,
                            value + 3,
                            value + 4,
                            value + 5,
                        ]
                    )
            bundle.writestr(
                f"S{number:03d}_synthetic.csv",
                "\n".join(",".join(map(str, r)) for r in rows) + "\n",
            )


def _fixture_windows(kind: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="harth-v21-fixture-") as directory:
        archive = Path(directory) / "synthetic.zip"
        _fixture_archive(archive, missing="140" if kind == "zero-support" else None)
        loaded = load_harth_archive(
            archive, CLASSES, protocol_hash=_sha(PROTOCOL), code_hash=_sha(Path(__file__))
        )
        return loaded.windows, loaded.manifest


def _publish(output: Path, result: dict[str, Any], guard: RunGuard) -> Path:
    """Stage all package members and publish the completion manifest last."""
    generated = json.loads(result["outputs"]["generator"])
    staging = output / ".staging"
    staging.mkdir(exist_ok=False)
    atomic_canonical_write(
        staging / "package-quarantine.json",
        {"status": "STAGED_NOT_CONSUMABLE", "completion_manifest": False},
    )
    guard.check_timeout()
    atomic_canonical_write(staging / "result-v2.1.json", result)
    guard.check_timeout()
    atomic_canonical_write(staging / "generator.json", generated)
    guard.check_timeout()
    atomic_canonical_write(
        staging / "quarantine.json",
        (
            {
                "quarantine": "guarded_real_unverified",
                "real_data": True,
                "performance_bearing": True,
                "scientific_status": "UNVERIFIED",
                "release": False,
            }
            if result.get("claim_boundary") == "guarded_real_quarantined_no_release"
            else {
                "quarantine": "synthetic_only_no_scientific_claim",
                "real_data": False,
                "performance_bearing": False,
                "scientific_status": "UNVERIFIED",
                "release": False,
            }
        ),
    )
    from howhow.episodes.harth.v2.run_guard import atomic_text_write

    guard.check_timeout()
    atomic_text_write(staging / "manuscript.md", result["outputs"]["manuscript"])
    guard.check_timeout()
    package_files = ["result-v2.1.json", "generator.json", "quarantine.json", "manuscript.md"]
    package_hashes = {name: _sha(staging / name) for name in package_files}
    # Move members before, but expose no completion signal until every member is
    # durable and the immutable manifest is written last.
    for name in package_files:
        guard.check_timeout()
        os.replace(staging / name, output / name)
    guard.check_timeout()
    manifest = {
        "manifest_version": "v2.1-package-1",
        "status": "COMPLETE",
        "immutable": True,
        "files": package_hashes,
    }
    atomic_canonical_write(output / "package-manifest.json", manifest)
    guard.check_timeout()
    (staging / "package-quarantine.json").unlink(missing_ok=True)
    staging.rmdir()
    guard.final(
        phase="complete",
        quarantine=(
            "guarded_real_unverified"
            if result.get("claim_boundary") == "guarded_real_quarantined_no_release"
            else "synthetic_only_no_scientific_claim"
        ),
    )
    return output / "result-v2.1.json"


def _claim_destination(output: Path, authorization: Mapping[str, Any]) -> Path:
    """Atomically create and mark the one-shot destination after auth only."""
    if output.exists():
        raise V21Error("FRESH_OUTPUT_REQUIRED")
    try:
        output.mkdir(parents=True, exist_ok=False)
        marker = output / ".howhow-v21-owned"
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_hash(dict(authorization)) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise V21Error("DESTINATION_RACE_OR_ALREADY_OWNED") from exc
    return output


def run_real(
    archive: Path, authorization: Path, output: Path, decision: Path | None = None
) -> Path:
    if os.environ.get(REAL_CONSENT_ENV) != "1":
        raise V21Error(f"--execute-real requires {REAL_CONSENT_ENV}=1")
    # Validate authorization before mkdir: new-schema auth failure leaves no output dir.
    auth = _load_authorization(authorization.resolve(), archive=archive.resolve(), output=output)
    if decision is None:
        raise V21Error("DECISION_FILE_REQUIRED")
    _load_decision(decision.resolve(), authorization=auth)
    _claim_destination(output, auth)
    guard = RunGuard(
        output,
        input_hash=_sha(archive),
        protocol_hash=_git_blob(PACKAGE_PATHS["protocol"]),
        code_hash=None,
    )
    try:
        code_hash, _revision = _code_identity()
        guard.code_hash = code_hash
        protocol_data, config_data, schema_data = (
            json.loads(PROTOCOL.read_text()),
            json.loads(CONFIG.read_text()),
            json.loads(SCHEMA.read_text()),
        )
        if (
            config_data.get("protocol_version") != PROTOCOL_VERSION
            or protocol_data.get("protocol_version") != PROTOCOL_VERSION
            or schema_data.get("$id") != SCHEMA_VERSION
        ):
            raise V21Error("PROTOCOL_CONFIG_SCHEMA_MISMATCH")
        loaded = load_harth_archive(
            archive,
            CLASSES,
            protocol_hash=_git_blob(PACKAGE_PATHS["protocol"]),
            code_hash=code_hash,
        )
        guard.stage("loader_complete", manifest=loaded.manifest)
        guard.bind_input_hash(input_hash(loaded.windows))
        guard.stage("engine_start")
        engine = run_protocol(
            loaded.windows,
            CLASSES,
            protocol_file=PROTOCOL,
            code_hash=code_hash,
            timeout_seconds=BUDGETS["timeout_seconds"],
        )
        guard.stage("engine_complete", folds=len(engine.folds))
        result = _build_result(
            engine,
            loaded.windows,
            loaded.manifest,
            code_hash,
            archive,
            claim="guarded_real_quarantined_no_release",
            execution_authorization=auth,
            real_data=True,
            timeout_check=guard.check_timeout,
        )
        return _publish(output, result, guard)
    except BaseException as exc:
        guard.failure(exc, phase="loader_engine_analysis_schema_generator_publication")
        raise


def run_synthetic(kind: str, output: Path) -> Path:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise V21Error("FRESH_OUTPUT_REQUIRED")
    output.mkdir(parents=True, exist_ok=True)
    windows, manifest = _fixture_windows(kind)
    guard = RunGuard(
        output,
        input_hash=canonical_hash(manifest),
        protocol_hash=_sha(PROTOCOL),
        code_hash=_sha(Path(__file__)),
    )
    try:
        if kind == "dirty-identity":
            raise V21Error("DIRTY_IDENTITY_REJECTED")
        if kind == "timeout":
            raise TimeoutError("fixed 1800s synthetic timeout gate")
        engine = run_protocol(
            windows,
            CLASSES,
            protocol_file=PROTOCOL,
            code_hash=_sha(Path(__file__)),
            timeout_seconds=1800.0,
        )
        result = _build_result(
            engine,
            windows,
            manifest,
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            Path(__file__),
            forced_status="INCOMPLETE_FAMILY" if kind == "incomplete-family" else None,
        )
        if kind == "schema-failure":
            result["unexpected"] = True
            validate_result(result)
        return _publish(output, result, guard)
    except BaseException as exc:
        guard.failure(exc, phase="loader_engine_analysis_schema_generator")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--synthetic-fixture",
        choices=(
            "complete",
            "zero-support",
            "incomplete-family",
            "schema-failure",
            "timeout",
            "dirty-identity",
        ),
        default="complete",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--decision", type=Path)
    parser.add_argument("--execute-real", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute_real:
            if args.archive is None or args.authorization is None:
                raise V21Error("REAL_ARCHIVE_AND_AUTHORIZATION_REQUIRED")
            run_real(args.archive, args.authorization, args.output, args.decision)
            print("V21 PASS: real execution completed")
        else:
            print(f"V21 PASS: {run_synthetic(args.synthetic_fixture, args.output)}")
    except BaseException as exc:
        print(f"V21 BLOCKED: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
