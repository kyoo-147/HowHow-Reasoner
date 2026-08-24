"""Fail-closed checks for the generated exploratory HARTH paper."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors = []
    tex = (ROOT / "main.tex").read_text(encoding="utf-8")
    generated = ROOT / "generated"
    for required in (
        "Introduction",
        "Related Work",
        "Research Questions and Claim Boundary",
        "Dataset and Attribution",
        "Methods",
        "Results",
        "Discussion and Custody Dissent",
        "Ethics",
        "Limitations",
        "Claim--Evidence and Data/Code Availability",
    ):
        if f"\\section{{{required}}}" not in tex:
            errors.append(f"missing section: {required}")
    for token in (
        "exploratory",
        "post-observation",
        "NOT\\_ESTIMABLE",
        "quarantine.json",
        "sufficient statistics",
        "generalization",
    ):
        if token not in tex:
            errors.append(f"missing boundary disclosure: {token}")
    if (
        "\\input{generated/macros}" not in tex
        or "\\input{generated/results}" not in tex
        or "\\input{generated/figures}" not in tex
    ):
        errors.append("generated outputs/macros not included")
    macros_path = generated / "macros.tex"
    macros_text = macros_path.read_text(encoding="utf-8") if macros_path.is_file() else ""
    if "resultSHA" not in macros_text:
        errors.append("generated macros missing")
    generated_text = (generated / "results.tex").read_text(encoding="utf-8")
    if (
        "NO SCIENTIFIC RESULTS" in tex
        or "Results withheld" in tex
        or "UNVERIFIED" in generated_text
    ):
        errors.append("stale protocol-only placeholder remains")
    exact_result_sha = "2d091df35ccafb8a912fa42cfc4e9bd993f6087923eca9119f1e8369c8d5dffd"
    if exact_result_sha not in macros_text.replace(r"\allowbreak{}", ""):
        errors.append("generated result SHA is not exact")
    if (
        "resultSubjects" not in tex
        or "bootstrapReplicates" not in tex
        or "signFlipDraws" not in tex
    ):
        errors.append("run quantities are not macro-owned")
    try:
        snap = json.loads((generated / "evidence-snapshot.json").read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"snapshot unreadable: {exc}")
        snap = {}
    if (
        snap.get("result_sha256")
        != "2d091df35ccafb8a912fa42cfc4e9bd993f6087923eca9119f1e8369c8d5dffd"
    ):
        errors.append("snapshot source SHA mismatch")
    if (
        snap.get("status") != "COMPLETE"
        or snap.get("claim_boundary") != "guarded_real_quarantined_no_release"
    ):
        errors.append("snapshot release boundary invalid")
    if len(snap.get("evidence", {})) < 39:
        errors.append("insufficient evidence pointers")
    for name in ("macros.tex", "results.tex", "figures.tex"):
        if not (generated / name).is_file() or "GENERATED" not in (generated / name).read_text(
            encoding="utf-8"
        ):
            errors.append(f"missing generated file: {name}")
    if errors:
        for e in errors:
            print("ERROR:", e)
        return 1
    print("paper checks passed: generated, custody-bound, exploratory manuscript")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
