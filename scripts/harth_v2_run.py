"""Fail-closed HARTH protocol-v2 preflight and run preparation.

This command validates the frozen protocol, input archive, resource budget, and
output/checkpoint ownership before a real engine is ever called.  Preflight is
safe and produces no scientific metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "episodes/harth-calibration/protocol/protocol-v2-proposal.json"
DEFAULT_CONFIG = ROOT / "episodes/harth-calibration/run-config-v2.json"
DEFAULT_ARCHIVE = ROOT / "episodes/harth-calibration/data/harth.zip"
DEFAULT_OUTPUT = ROOT / "episodes/harth-calibration/artifacts/v2-run"
BOOTSTRAP_REPS = 2000
REAL_CONSENT_ENV = "HOWHOW_RUN_REAL_HARTH"


class PreflightFailure(ValueError):
    """A required immutable input or safety invariant is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def git(*args: str) -> str:
    result = subprocess.run(("git", *args), cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def versions() -> dict[str, str]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "git": git("--version"),
        "ruff": _tool_version("ruff"),
        "pytest": _module_version("pytest"),
        "numpy": _module_version("numpy"),
    }


def _tool_version(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        return "UNAVAILABLE"
    return subprocess.run((executable, "--version"), text=True, capture_output=True).stdout.strip()


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
        return str(getattr(module, "__version__", "unknown"))
    except ImportError:
        return "UNAVAILABLE"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightFailure(f"cannot read JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightFailure(f"JSON input must be an object: {path}")
    return value


def validate_config(
    config: dict[str, Any], protocol: dict[str, Any], args: argparse.Namespace
) -> None:
    if config.get("protocol_id") != protocol.get("protocol_id"):
        raise PreflightFailure("config protocol_id does not match frozen protocol")
    if config.get("seed") != args.seed or args.seed != 0:
        raise PreflightFailure("seed must equal frozen seed 0")
    if args.bootstrap_reps != BOOTSTRAP_REPS or config.get("bootstrap_reps") != BOOTSTRAP_REPS:
        raise PreflightFailure("bootstrap repetitions must be exactly 2000")
    budget = protocol.get("budget", {})
    if args.max_outer_folds != budget.get("max_outer_folds", 22):
        raise PreflightFailure("outer-fold budget does not match frozen protocol")
    if config.get("sensor_configurations") != budget.get("sensor_configurations", 3):
        raise PreflightFailure("sensor configuration count does not match frozen protocol")
    if config.get("calibration_states") != budget.get("calibration_states", 2):
        raise PreflightFailure("calibration state count does not match frozen protocol")
    if args.wall_clock_minutes != budget.get("wall_clock_minutes", 30):
        raise PreflightFailure("wall-clock budget does not match frozen protocol")
    if config.get("fold_strategy") != "nested_subject_held_out_loso":
        raise PreflightFailure("fold strategy is not the frozen nested LOSO strategy")


def validate_archive(archive: Path, config: dict[str, Any]) -> dict[str, Any]:
    if not archive.is_file():
        raise PreflightFailure(f"archive is missing: {archive}")
    actual = sha256_file(archive)
    expected = config.get("archive_sha256")
    if not expected or expected == "REQUIRED_OPERATOR_CHECKSUM":
        raise PreflightFailure("archive_sha256 must be supplied; unknown checksum is not verified")
    if actual != expected:
        raise PreflightFailure(f"archive checksum mismatch: expected {expected}, got {actual}")
    return {"path": str(archive), "sha256": actual, "bytes": archive.stat().st_size}


def validate_output(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise PreflightFailure(f"output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        raise PreflightFailure(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)


def build_manifest(
    args: argparse.Namespace, protocol: Path, config: Path, archive: dict[str, Any]
) -> dict[str, Any]:
    return {
        "status": "PASS",
        "scientific_metrics": False,
        "real_execution": False,
        "started_at_utc": utc_now(),
        "finished_at_utc": utc_now(),
        "argv": list(sys.argv),
        "git_revision": git("rev-parse", "HEAD"),
        "git_status": git("status", "--porcelain"),
        "protocol": {"path": str(protocol), "sha256": sha256_file(protocol)},
        "config": {"path": str(config), "sha256": sha256_file(config)},
        "archive": archive,
        "budget": {
            "max_outer_folds": args.max_outer_folds,
            "wall_clock_minutes": args.wall_clock_minutes,
            "bootstrap_reps": args.bootstrap_reps,
        },
        "checkpoint": str(args.checkpoint),
        "seed": args.seed,
        "environment": versions(),
        "process": {"pid": os.getpid(), "executable": sys.executable},
    }


def preflight(args: argparse.Namespace) -> Path:
    started = time.monotonic()
    protocol = args.protocol.resolve()
    config = args.config.resolve()
    archive_path = args.archive.resolve()
    if git("status", "--porcelain"):
        raise PreflightFailure("working tree is dirty; refusing run preparation")
    if not protocol.is_file() or not config.is_file():
        raise PreflightFailure("frozen protocol or run config is missing")
    protocol_data = load_json(protocol)
    config_data = load_json(config)
    validate_config(config_data, protocol_data, args)
    archive = validate_archive(archive_path, config_data)
    output = args.output.resolve()
    validate_output(output)
    args.checkpoint = args.checkpoint.resolve()
    if args.checkpoint.parent != output:
        raise PreflightFailure("checkpoint must be inside the clean output directory")
    manifest = build_manifest(args, protocol, config, archive)
    manifest["duration_seconds"] = time.monotonic() - started
    path = output / "preflight.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_OUTPUT / "checkpoint.json")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--bootstrap-reps", type=int, default=BOOTSTRAP_REPS)
    parser.add_argument("--max-outer-folds", type=int, default=22)
    parser.add_argument("--wall-clock-minutes", type=int, default=30)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--execute-real", action="store_true")
    return parser


def main() -> int:
    args = parser().parse_args()
    if not args.preflight_only and not args.execute_real:
        print("refusing: choose --preflight-only or guarded --execute-real", file=sys.stderr)
        return 2
    try:
        path = preflight(args)
        if args.execute_real:
            if os.environ.get(REAL_CONSENT_ENV) != "1":
                raise PreflightFailure(f"--execute-real requires {REAL_CONSENT_ENV}=1")
            raise PreflightFailure(
                "real engine execution is intentionally disabled in this preparation layer"
            )
        print(f"PREFLIGHT PASS: {path}")
        return 0
    except (OSError, PreflightFailure, subprocess.CalledProcessError) as exc:
        output = args.output.resolve()
        if output.is_dir() and not any(output.iterdir()):
            failure = {
                "status": "BLOCKED",
                "scientific_metrics": False,
                "started_at_utc": utc_now(),
                "finished_at_utc": utc_now(),
                "argv": list(sys.argv),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "process": {"pid": os.getpid(), "executable": sys.executable},
            }
            (output / "failure.json").write_text(
                json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        print(f"PREFLIGHT BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
