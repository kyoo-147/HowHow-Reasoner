"""Run the complete deterministic local Phase 0 validation suite."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"required executable not found: {name}; run `uv sync --locked` first")
    return path


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    ruff = executable("ruff")
    mypy = executable("mypy")
    run(ruff, "format", "--check", ".")
    run(ruff, "check", ".")
    run(mypy, "src")
    run(sys.executable, "-m", "pytest", "tests")
    run(sys.executable, "-m", "pytest", "tests/test_schema_drift.py")


if __name__ == "__main__":
    main()
