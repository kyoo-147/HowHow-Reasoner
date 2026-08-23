from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PAPER = Path(__file__).resolve().parents[1]
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
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "v2",
                "status": "VALIDATED",
                "protocol_version": "protocol-v2",
                "gates": {
                    name: True
                    for name in (
                        "provenance",
                        "frozen_split",
                        "leakage",
                        "finite_metrics",
                        "class_coverage",
                    )
                },
                "folds": [{"subject": "S1", "metrics": {"nll": 0.25, "brier": 0.1, "ece": 0.05}}],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(GENERATOR), "--artifact", str(artifact)], check=True)
    text = (PAPER / "generated" / "results.tex").read_text(encoding="utf-8")
    assert "S1 & 0.250000 & 0.100000 & 0.050000" in text
    subprocess.run([sys.executable, str(GENERATOR)], check=True)


def test_structural_checker_passes() -> None:
    assert subprocess.run([sys.executable, str(CHECKER)], check=False).returncode == 0
