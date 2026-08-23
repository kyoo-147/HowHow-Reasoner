"""Run the authoritative local Python and frontend validation gates.

This intentionally names tracked source/test paths instead of scanning ``.`` so
machine-local, ignored, or untracked evidence cannot affect the result.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNPM = "pnpm.cmd" if os.name == "nt" else "pnpm"


def tracked_python_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        path
        for path in result.stdout.splitlines()
        if path.startswith(("src/", "scripts/", "tests/"))
    ]


def run(label: str, *command: str) -> int:
    print(f"CHECK {label}: {' '.join(command)}", flush=True)
    try:
        completed = subprocess.run(command, cwd=ROOT)
    except FileNotFoundError:
        print(f"FAIL {label}: executable not found: {command[0]}", flush=True)
        return 127
    if completed.returncode:
        print(f"FAIL {label}: exit {completed.returncode}", flush=True)
    else:
        print(f"PASS {label}", flush=True)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-miktex",
        action="store_true",
        help="run optional tests requiring a real local MiKTeX installation",
    )
    args = parser.parse_args()

    python_paths = tracked_python_paths()
    checks: list[tuple[str, list[str]]] = [
        ("python-format", ["ruff", "format", "--check", *python_paths]),
        ("python-lint", ["ruff", "check", *python_paths]),
        ("python-types", ["mypy", "src"]),
        ("python-tests", [sys.executable, "-m", "pytest", "tests", "-m", "not real_miktex"]),
        ("frontend-install", [PNPM, "install", "--frozen-lockfile"]),
        ("frontend-typecheck", [PNPM, "--dir", "apps/web", "typecheck"]),
        ("frontend-lint", [PNPM, "--dir", "apps/web", "lint"]),
        ("frontend-test", [PNPM, "--dir", "apps/web", "test"]),
        ("frontend-build", [PNPM, "--dir", "apps/web", "build"]),
    ]
    if args.real_miktex:
        checks.append(
            ("real-miktex", [sys.executable, "-m", "pytest", "tests", "-m", "real_miktex"])
        )
    else:
        print("SKIP real-miktex: pass --real-miktex to run optional local MiKTeX tests", flush=True)

    for label, command in checks:
        if (status := run(label, *command)) != 0:
            return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
