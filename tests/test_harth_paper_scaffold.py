from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_isolated_public_copy_has_no_source_dependencies(tmp_path: Path):
    copied = tmp_path / "paper"
    import shutil

    shutil.copytree(PAPER, copied)
    shutil.rmtree(copied / "tools" / "__pycache__", ignore_errors=True)
    script = copied / "tools" / "generate_tables.py"
    check = copied / "tools" / "check_paper.py"
    assert subprocess.run([sys.executable, str(script), "--check"], check=False).returncode == 0
    assert subprocess.run([sys.executable, str(script), "--render"], check=False).returncode == 0
    assert subprocess.run([sys.executable, str(check)], check=False).returncode == 0


def test_public_tamper_is_detected(tmp_path: Path):
    copied = tmp_path / "paper"
    import shutil

    shutil.copytree(PAPER, copied)
    output = copied / "generated" / "results.tex"
    output.write_text(output.read_text().replace("0.0332", "0.0333", 1))
    assert (
        subprocess.run(
            [sys.executable, str(copied / "tools/generate_tables.py"), "--check"], check=False
        ).returncode
        != 0
    )


def test_generated_outputs_have_figures_and_no_placeholder():
    results = (PAPER / "generated/results.tex").read_text()
    figures = (PAPER / "generated/figures.tex").read_text()
    assert "UNVERIFIED" not in results
    assert "resultSHA" in (PAPER / "generated/macros.tex").read_text()
    assert figures.count("\\begin{figure}") == 2
    assert "No validator-approved" not in results


@pytest.mark.parametrize(
    ("label", "needle", "replacement"),
    [
        ("snapshot", '"schema": "howhow-harth-publication-snapshot-v2.1"', '"schema": "tampered"'),
        ("pointer", '"source_pointer": "#/inference/', '"source_pointer": "#/inference/tampered/'),
        ("numeric", '"bootstrap_replicates": 2000', '"bootstrap_replicates": 2001'),
    ],
)
def test_public_snapshot_tamper_classes_fail_closed(
    tmp_path: Path, label: str, needle: str, replacement: str
):
    copied = tmp_path / label
    import shutil

    shutil.copytree(PAPER, copied)
    snapshot = copied / "generated/evidence-snapshot.json"
    text = snapshot.read_text(encoding="utf-8")
    assert needle in text
    snapshot.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    assert (
        subprocess.run(
            [sys.executable, str(copied / "tools/generate_tables.py"), "--check"], check=False
        ).returncode
        != 0
    )


def test_output_and_custody_hash_tamper_fail_closed(tmp_path: Path):
    import shutil

    copied = tmp_path / "paper"
    shutil.copytree(PAPER, copied)
    output = copied / "generated/results.tex"
    output.write_text(
        output.read_text(encoding="utf-8").replace("0.0332", "0.0333", 1), encoding="utf-8"
    )
    assert (
        subprocess.run(
            [sys.executable, str(copied / "tools/generate_tables.py"), "--check"], check=False
        ).returncode
        != 0
    )
    fake_result = tmp_path / "result.json"
    fake_custody = tmp_path / "custody.json"
    fake_result.write_text("{}", encoding="utf-8")
    fake_custody.write_text("{}", encoding="utf-8")
    assert (
        subprocess.run(
            [
                sys.executable,
                str(copied / "tools/generate_tables.py"),
                "--result",
                str(fake_result),
                "--custody",
                str(fake_custody),
            ],
            check=False,
        ).returncode
        != 0
    )
