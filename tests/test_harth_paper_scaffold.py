from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PAPER = Path(__file__).resolve().parents[1] / "episodes" / "harth-calibration" / "paper"
GENERATOR = PAPER / "tools" / "generate_tables.py"
CHECKER = PAPER / "tools" / "check_paper.py"


def test_generated_package_is_reproducible_and_bound():
    assert subprocess.run([sys.executable, str(GENERATOR), "--check"], check=False).returncode == 0
    snap = json.loads((PAPER / "generated/evidence-snapshot.json").read_text())
    assert (
        snap["result_sha256"] == "2d091df35ccafb8a912fa42cfc4e9bd993f6087923eca9119f1e8369c8d5dffd"
    )
    assert len(snap["evidence"]) >= 39


def test_unavailable_or_drifted_release_fails_closed(tmp_path: Path):
    missing = tmp_path / "missing.json"
    assert (
        subprocess.run(
            [sys.executable, str(GENERATOR), "--result", str(missing)], check=False
        ).returncode
        != 0
    )


def test_paper_checker_passes():
    assert subprocess.run([sys.executable, str(CHECKER)], check=False).returncode == 0


def test_generated_outputs_have_figures_and_no_placeholder():
    results = (PAPER / "generated/results.tex").read_text()
    figures = (PAPER / "generated/figures.tex").read_text()
    assert "UNVERIFIED" in results
    assert figures.count("\\begin{figure}") == 2
    assert "No validator-approved" not in results
