"""Compile a clean paper copy and verify the final TeX pass is resolved."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", type=Path, required=True)
    args = parser.parse_args()
    staging = args.staging.resolve()
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(ROOT, staging)
    latexmk = shutil.which("latexmk")
    pdflatex = shutil.which("pdflatex")
    if not latexmk or not pdflatex:
        print("MiKTeX tools unavailable")
        return 2
    build = subprocess.run(
        [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
        cwd=staging,
        capture_output=True,
        text=True,
        check=False,
    )
    (staging / "latexmk.log").write_text(build.stdout + build.stderr, encoding="utf-8")
    final = subprocess.run(
        [pdflatex, "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
        cwd=staging,
        capture_output=True,
        text=True,
        check=False,
    )
    final_output = final.stdout + final.stderr
    (staging / "final-pdflatex.log").write_text(final_output, encoding="utf-8")
    final_forbidden = (
        "overfull \\hbox",
        "undefined",
        "empty `thebibliography'",
        "empty bibliography",
        "no file name",
        "fatal error",
        "emergency stop",
    )
    bibliography_forbidden = ("to sort, need author or key", "empty author")
    found = [marker for marker in final_forbidden if marker in final_output.lower()]
    found.extend(
        marker
        for marker in bibliography_forbidden
        if marker in (build.stdout + build.stderr).lower()
    )
    resolved = not found
    pdf = staging / "main.pdf"
    if (
        build.returncode
        or final.returncode
        or not pdf.is_file()
        or pdf.stat().st_size == 0
        or not resolved
    ):
        print(
            "compile failed: "
            f"latexmk={build.returncode} pdflatex={final.returncode} forbidden={found}"
        )
        return 1
    print(f"compile passed: pdf={pdf} bytes={pdf.stat().st_size} staging={staging}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
