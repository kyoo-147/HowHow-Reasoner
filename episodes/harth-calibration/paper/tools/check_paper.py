"""Fail-closed structural checks for the HARTH paper scaffold."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    tex = (ROOT / "main.tex").read_text(encoding="utf-8")
    generated = ROOT / "generated" / "results.tex"
    errors: list[str] = []
    for required in (
        "abstract",
        "Introduction",
        "Related Work",
        "Research Questions",
        "Protocol-v2 Methods",
        "Dataset and Provenance",
        "Ethics and License",
        "Planned Analysis",
        "Reproducibility",
        "Limitations",
        "Reviewer and Dissent Record",
        "Claim--Evidence Map",
    ):
        if required not in tex:
            errors.append(f"missing section: {required}")
    if "\\input{generated/results}" not in tex:
        errors.append("main.tex must include generated results")
    if not generated.is_file() or "UNVERIFIED" not in generated.read_text(encoding="utf-8"):
        errors.append("default results output must be a fail-closed UNVERIFIED placeholder")
    if "No scientific results" not in tex and "NO SCIENTIFIC RESULTS" not in tex:
        errors.append("status banner must reject scientific results")
    bib = (ROOT / "references.bib").read_text(encoding="utf-8")
    cited = set(re.findall(r"\\cite\{([^}]+)\}", tex))
    keys = set(re.findall(r"@\w+\{([^,]+),", bib))
    for group in cited:
        for key in group.split(","):
            if key.strip() not in keys:
                errors.append(f"missing bibliography key: {key.strip()}")
    matrix = json.loads(
        (ROOT.parent / "literature" / "related-work-matrix.json").read_text(encoding="utf-8")
    )
    if matrix.get("status") != "UNVERIFIED":
        errors.append("literature matrix status was promoted")
    if "UNVERIFIED" not in tex:
        errors.append("manuscript must preserve evidence status")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("paper scaffold checks passed (manuscript mechanics; scientific evidence UNVERIFIED)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
