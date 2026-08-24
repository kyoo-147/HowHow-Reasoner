from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PAPER = Path(__file__).resolve().parents[1] / "episodes" / "harth-calibration" / "paper"
GENERATOR = PAPER / "tools" / "generate_tables.py"
CHECKER = PAPER / "tools" / "check_paper.py"


def load_generator(path: Path):
    spec = importlib.util.spec_from_file_location("harth_paper_generator_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    shutil.copytree(PAPER, copied)
    shutil.rmtree(copied / "tools" / "__pycache__", ignore_errors=True)
    script = copied / "tools" / "generate_tables.py"
    check = copied / "tools" / "check_paper.py"
    assert subprocess.run([sys.executable, str(script), "--check"], check=False).returncode == 0
    assert subprocess.run([sys.executable, str(script), "--render"], check=False).returncode == 0
    assert subprocess.run([sys.executable, str(check)], check=False).returncode == 0


def test_public_tamper_is_detected(tmp_path: Path):
    copied = tmp_path / "paper"
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


def test_generated_validation_prose_has_single_tex_command_prefixes():
    results = (PAPER / "generated/results.tex").read_text(encoding="utf-8")
    for command in (r"\ref{tab:inference}", r"\texttt{NOT\_ESTIMABLE}", r"\paragraph{"):
        assert command in results
    assert re.search(r"\\\\(?:ref|texttt|paragraph)\b", results) is None


def test_primary_estimates_equal_exact_paired_snapshot_values():
    generator = load_generator(GENERATOR)
    snapshot = json.loads((PAPER / "generated/evidence-snapshot.json").read_text())
    metrics = {"H_NLL": "nll", "H_BRIER": "brier", "H_ECE": "ece"}
    for hypothesis, metric in metrics.items():
        key = (
            f"paired_delta|{metric}|calibrated_vs_uncalibrated|full_sensor|calibrated|"
            "full_sensor|uncalibrated|seed=0"
        )
        assert snapshot["holm"][hypothesis]["estimate_source_pointer"] == (
            f"#/paired/{key}/estimate"
        )
        assert (
            generator.paired_estimate_for_hypothesis(snapshot, hypothesis)
            == (snapshot["paired"][key]["estimate"])
        )


def test_source_build_finalizes_hashes_and_stale_manifest_fails(tmp_path: Path):
    copied = tmp_path / "paper"
    shutil.copytree(PAPER, copied)
    shutil.rmtree(copied / "tools" / "__pycache__", ignore_errors=True)
    generator = load_generator(copied / "tools" / "generate_tables.py")
    outputs = {
        path: path.read_text(encoding="utf-8")
        for path in (generator.SNAPSHOT, generator.MACROS, generator.RESULTS, generator.FIGURES)
    }
    generator.write_source_build(outputs)
    manifest_path = copied / "arxiv-source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative in (
        "generated/evidence-snapshot.json",
        "generated/macros.tex",
        "generated/results.tex",
        "generated/figures.tex",
    ):
        assert (
            manifest["sha256"][relative]
            == hashlib.sha256((copied / relative).read_bytes()).hexdigest()
        )
    manifest["sha256"]["generated/results.tex"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    assert (
        subprocess.run(
            [sys.executable, str(copied / "tools/generate_tables.py"), "--check"], check=False
        ).returncode
        != 0
    )


@pytest.mark.parametrize("required_source", ["main.tex", "references.bib"])
def test_source_build_rejects_incomplete_include_and_hash_scope(
    tmp_path: Path, required_source: str
):
    copied = tmp_path / required_source.replace(".", "-")
    shutil.copytree(PAPER, copied)
    generator = load_generator(copied / "tools" / "generate_tables.py")
    manifest_path = copied / "arxiv-source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["include"].remove(required_source)
    del manifest["sha256"][required_source]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    outputs = {
        path: path.read_text(encoding="utf-8")
        for path in (generator.SNAPSHOT, generator.MACROS, generator.RESULTS, generator.FIGURES)
    }
    with pytest.raises(RuntimeError, match="include scope mismatch"):
        generator.write_source_build(outputs)


def test_invalid_manifest_does_not_mutate_source_build_outputs(tmp_path: Path):
    copied = tmp_path / "paper"
    shutil.copytree(PAPER, copied)
    generator = load_generator(copied / "tools" / "generate_tables.py")
    output_paths = (generator.SNAPSHOT, generator.MACROS, generator.RESULTS, generator.FIGURES)
    before = {path: path.read_bytes() for path in output_paths}
    manifest_path = copied / "arxiv-source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "INVALID"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    invalid_manifest_bytes = manifest_path.read_bytes()
    changed_outputs = {path: "changed\n" for path in output_paths}

    with pytest.raises(RuntimeError, match="status mismatch"):
        generator.write_source_build(changed_outputs)

    assert {path: path.read_bytes() for path in output_paths} == before
    assert manifest_path.read_bytes() == invalid_manifest_bytes


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
