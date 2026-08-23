from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PAPER = Path(__file__).resolve().parents[1] / "episodes" / "harth-calibration" / "paper"
GENERATOR = PAPER / "tools" / "generate_tables.py"
CHECKER = PAPER / "tools" / "check_paper.py"


def test_default_generator_is_fail_closed(tmp_path: Path) -> None:
    output = PAPER / "generated" / "results.tex"
    before = output.read_text(encoding="utf-8")
    assert (
        subprocess.run([sys.executable, str(GENERATOR), "--check-only"], check=False).returncode
        == 0
    )
    assert "UNVERIFIED" in before


def test_invalid_artifact_cannot_generate_metrics(tmp_path: Path) -> None:
    artifact = tmp_path / "results.json"
    artifact.write_text(
        json.dumps({"status": "VALIDATED", "folds": [{"subject": "S1", "metrics": {"nll": 1}}]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--artifact", str(artifact)], check=False
    )
    assert result.returncode == 0
    assert "UNVERIFIED" in (PAPER / "generated" / "results.tex").read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(GENERATOR)], check=True)


def test_valid_artifact_generates_only_machine_values(tmp_path: Path) -> None:
    artifact = tmp_path / "results.json"
    digest = "a" * 64
    state = {
        "metrics": {"nll": 0.25, "brier": 0.1, "ece": 0.05, "accuracy": 0.5, "macro_f1": 0.4},
        "interval": [0.2, 0.3],
        "class_support": {"rest": 3, "walk": 3},
    }
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "harth-result-v1",
                "status": "VALIDATED",
                "protocol_id": "harth-calibration-v2",
                "input_hash": digest,
                "protocol_hash": digest,
                "code_hash": digest,
                "provenance": {
                    "input_hash": digest,
                    "protocol_hash": digest,
                    "code_hash": digest,
                    "source": "test",
                },
                "class_vocabulary": ["rest", "walk"],
                "fold_ids": ["full_sensor::S1"],
                "folds": [
                    {
                        "fold_id": "full_sensor::S1",
                        "configuration": "full_sensor",
                        "test_subject": "S1",
                        "train_subjects": ["S2"],
                        "temperature": 1.0,
                        "optimizer": {"converged": True},
                        "states": {"uncalibrated": state, "calibrated": state},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(GENERATOR), "--artifact", str(artifact)], check=True)
    text = (PAPER / "generated" / "results.tex").read_text(encoding="utf-8")
    assert "full_sensor::S1 & 0.250000 & 0.100000 & 0.050000" in text
    subprocess.run([sys.executable, str(GENERATOR)], check=True)


def test_structural_checker_passes() -> None:
    assert subprocess.run([sys.executable, str(CHECKER)], check=False).returncode == 0
