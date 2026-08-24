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
from jsonschema import Draft202012Validator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
DECISION_ID = "howhow-harth-v21-pr42-review-4"
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
    if auth["decision_id"] != DECISION_ID or auth["decision_sha256"] != APPROVED_DECISION_SHA256:
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
    held_labels = [w.label for w in windows if w.subject == subjects[0]]
    train_labels = [w.label for w in windows if w.subject != subjects[0]]
    held_support = support_gate(held_labels, CLASSES, stage="held_out_test", minimum=2)
    support_status = "NOT_ESTIMABLE" if held_support["zero_support"] else None
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
    pairing = pairing_manifest(records)
    raw = {
        "H_" + m.upper(): pvalue(
            {s: values[m]["calibrated"][s] - values[m]["uncalibrated"][s] for s in subjects},
            estimand=m.upper(),
        )
        for m in ESTIMANDS
    }
    raw_p = {k: row["p_value"] for k, row in raw.items() if row.get("status") == "ESTIMABLE"}
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
    status = (
        support_status or forced_status or ("COMPLETE" if len(raw_p) == 3 else "INCOMPLETE_FAMILY")
    )
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
        "engine": engine.to_dict(),
        "support": {
            "training": support_gate(train_labels, CLASSES, stage="training", minimum=2),
            "inner_calibration": support_gate(
                train_labels, CLASSES, stage="inner_calibration", minimum=2
            ),
            "held_out_test": held_support,
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
            "pairing_manifest_hash": canonical_hash(pairing),
            "eligible_subject_hash": canonical_hash(subjects),
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
    if timeout_check is not None:
        timeout_check()
    generated = generate_outputs(result)
    if timeout_check is not None:
        timeout_check()
    result["outputs"] = {
        "generator": generated["generator.json"],
        "manuscript": generated["manuscript.md"],
    }
    validate_result(
        result,
        artifacts=_artifacts(
            archive, code_hash, eligibility=eligibility, pairing=pairing, input_data=manifest
        ),
    )
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
    generated = json.loads(result["outputs"]["generator"])
    atomic_canonical_write(output / "result-v2.1.json", result)
    atomic_canonical_write(output / "generator.json", generated)
    atomic_canonical_write(
        output / "quarantine.json",
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

    atomic_text_write(output / "manuscript.md", result["outputs"]["manuscript"])
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


def run_real(archive: Path, authorization: Path, output: Path) -> Path:
    if os.environ.get(REAL_CONSENT_ENV) != "1":
        raise V21Error(f"--execute-real requires {REAL_CONSENT_ENV}=1")
    # Authorization is completely validated before mkdir: new-schema auth failure leaves no output dir.
    legacy = False
    try:
        raw = json.loads(authorization.read_text(encoding="utf-8"))
        legacy = isinstance(raw, dict) and "decision_id" not in raw and "allow_rerun" in raw
    except (OSError, json.JSONDecodeError):
        pass
    try:
        auth = _load_authorization(
            authorization.resolve(), archive=archive.resolve(), output=output
        )
    except BaseException as exc:
        # Compatibility-only failure envelope for pre-v2.1 records; such records are never executed.
        if legacy:
            output.mkdir(parents=True, exist_ok=True)
            from howhow.episodes.harth.v2.run_guard import atomic_write

            atomic_write(
                output / "failure.json",
                {
                    "status": "FAILED",
                    "scientific_metrics": False,
                    "phase": "authorization",
                    "error": str(exc),
                },
            )
        raise
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
    parser.add_argument("--execute-real", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute_real:
            if args.archive is None or args.authorization is None:
                raise V21Error("REAL_ARCHIVE_AND_AUTHORIZATION_REQUIRED")
            run_real(args.archive, args.authorization, args.output)
            print("V21 PASS: real execution completed")
        else:
            print(f"V21 PASS: {run_synthetic(args.synthetic_fixture, args.output)}")
    except BaseException as exc:
        print(f"V21 BLOCKED: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
