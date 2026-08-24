"""Synthetic-only executable HARTH protocol-v2.1 integration.

This command is deliberately incapable of opening the HARTH archive or resuming a run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from howhow.episodes.harth.v2 import RunGuard, load_harth_archive, run_protocol
from howhow.episodes.harth.v21 import (
    APPROVED_DECISION_SHA256,
    APPROVED_PROPOSAL_SHA256,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    V21Error,
    atomic_canonical_write,
    build_artifact_hashes,
    canonical_hash,
    generate_outputs,
    holm,
    pairing_manifest,
    support_gate,
    validate_result,
    window_set_hash,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "episodes/harth-calibration/protocol/protocol-v2.1.json"
CONFIG = ROOT / "episodes/harth-calibration/run-config-v2.1.json"
SCHEMA = ROOT / "schemas/v2.1/Result.json"
CLASSES = ["1", "2", "3", "4", "5", "6", "7", "8", "13", "14", "130", "140"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            subject = f"S{number:03d}"
            rows: list[list[Any]] = [header]
            tick = datetime(2024, 1, 1, tzinfo=UTC)
            for cls_index, label in enumerate(CLASSES):
                if label == missing and number == 1:
                    continue
                for i in range(128):
                    value = float(cls_index * 10 + number / 100 + i / 10000)
                    rows.append(
                        [
                            (tick + timedelta(seconds=len(rows))).isoformat(),
                            subject,
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
            text = "\n".join(",".join(map(str, row)) for row in rows) + "\n"
            bundle.writestr(f"{subject}_synthetic.csv", text)


def _fixture_windows(kind: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if kind not in {
        "complete",
        "zero-support",
        "incomplete-family",
        "schema-failure",
        "timeout",
        "dirty-identity",
    }:
        raise V21Error(f"unknown synthetic fixture: {kind}")
    with tempfile.TemporaryDirectory(prefix="harth-v21-fixture-") as directory:
        archive = Path(directory) / "synthetic.zip"
        _fixture_archive(archive, missing="140" if kind == "zero-support" else None)
        loaded = load_harth_archive(
            archive, CLASSES, protocol_hash=_sha(PROTOCOL), code_hash=_sha(Path(__file__))
        )
        # Copy values out before the temporary fixture is removed. Loader is metrics-free.
        return loaded.windows, loaded.manifest


def _hypotheses() -> dict[str, Any]:
    return holm({"H_NLL": 1.0, "H_BRIER": 1.0, "H_ECE": 1.0})


def _result(windows: tuple[Any, ...], manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    subjects = sorted({w.subject for w in windows})
    held_labels = [w.label for w in windows if w.subject == subjects[0]]
    training_labels = [w.label for w in windows if w.subject != subjects[0]]
    support = {
        "training": support_gate(training_labels, CLASSES, stage="training", minimum=2),
        "inner_calibration": support_gate(
            training_labels, CLASSES, stage="inner_calibration", minimum=2
        ),
        "held_out_test": support_gate(held_labels, CLASSES, stage="held_out_test", minimum=2),
    }
    eligibility = {"subjects": subjects, "rule": "synthetic_all_subjects_complete_windows"}
    records = []
    window_hash = window_set_hash([[w.provenance] for w in windows if w.subject == subjects[0]])
    for estimand in ("nll", "brier", "ece"):
        for arm in ("calibrated", "uncalibrated"):
            records.append(
                {
                    "subject_id": subjects[0],
                    "contrast_id": "calibrated_vs_uncalibrated",
                    "estimand_id": estimand,
                    "reason": "" if kind != "zero-support" else "ESTIMAND_NOT_ESTIMABLE",
                    "arm": arm,
                    "window_set_hash": window_hash,
                    "population_rule_id": "synthetic_all_subjects_complete_windows",
                }
            )
    pairing = pairing_manifest(records)
    hashes = build_artifact_hashes(
        protocol=json.loads(PROTOCOL.read_text()),
        schema=json.loads(SCHEMA.read_text()),
        config=json.loads(CONFIG.read_text()),
        code=Path(__file__).read_bytes(),
        input_data=manifest,
        vocabulary=CLASSES,
        eligibility_manifest=eligibility,
        pairing=pairing,
    )
    family = _hypotheses()
    estimable = kind == "complete"
    status = (
        "COMPLETE"
        if estimable
        else ("INCOMPLETE_FAMILY" if kind == "incomplete-family" else "NOT_ESTIMABLE")
    )
    generated = generate_outputs({"status": status})
    outputs = {"generator": generated["generator.json"], "manuscript": generated["manuscript.md"]}
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
        "hashes": hashes,
        "support": support,
        "estimability": {
            metric: (
                {"status": "ESTIMABLE", "reason": "SUFFICIENT_ELIGIBLE_SUBJECTS", "value": 0.0}
                if estimable
                else {"status": "NOT_ESTIMABLE", "reason": "INSUFFICIENT_ELIGIBLE_SUBJECTS"}
            )
            for metric in ("nll", "brier", "ece")
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
            "status": "COMPLETE" if kind != "incomplete-family" else "INCOMPLETE_FAMILY",
        },
        "outputs": outputs,
        "claim_boundary": "synthetic_structural_only_no_performance_claim",
    }
    return result


def run_synthetic(kind: str, output: Path) -> Path:
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise V21Error("FRESH_OUTPUT_REQUIRED")
    output.mkdir(parents=True, exist_ok=True)
    protocol_data, config_data = json.loads(PROTOCOL.read_text()), json.loads(CONFIG.read_text())
    if (
        config_data.get("protocol_version") != PROTOCOL_VERSION
        or protocol_data.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise V21Error("PROTOCOL_BINDING_MISMATCH")
    windows, manifest = _fixture_windows(kind)
    guard = RunGuard(
        output,
        input_hash=canonical_hash(manifest),
        protocol_hash=_sha(PROTOCOL),
        code_hash=_sha(Path(__file__)),
    )
    try:
        guard.stage("loader_complete", manifest=manifest)
        if kind == "dirty-identity":
            raise V21Error("DIRTY_IDENTITY_REJECTED")
        if kind == "timeout":
            raise TimeoutError("fixed 1800s synthetic timeout gate")
        result = run_protocol(
            windows,
            CLASSES,
            protocol_file=PROTOCOL,
            code_hash=_sha(Path(__file__)),
            timeout_seconds=1800.0,
        )
        guard.stage("engine_complete", folds=len(result.folds))
        artifact = _result(windows, manifest, kind)
        if kind == "schema-failure":
            artifact["unexpected"] = True
        validate_result(artifact)
        generated = generate_outputs(artifact)
        artifact["outputs"] = {
            "generator": generated["generator.json"],
            "manuscript": generated["manuscript.md"],
        }
        validate_result(artifact)
        atomic_canonical_write(output / "result-v2.1.json", artifact)
        atomic_canonical_write(output / "generator.json", json.loads(generated["generator.json"]))
        atomic_canonical_write(
            output / "quarantine.json",
            {
                "quarantine": "synthetic_only_no_scientific_claim",
                "real_data": False,
                "scientific_metrics": False,
            },
        )
        (output / "manuscript.md").write_text(
            generated["manuscript.md"], encoding="utf-8", newline="\n"
        )
        guard.final(phase="complete", quarantine="synthetic_only_no_scientific_claim")
        return output / "result-v2.1.json"
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
    args = parser.parse_args()
    try:
        path = run_synthetic(args.synthetic_fixture, args.output.resolve())
    except BaseException as exc:
        print(f"V21 BLOCKED: {type(exc).__name__}", file=__import__("sys").stderr)
        return 1
    print(f"V21 PASS: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
