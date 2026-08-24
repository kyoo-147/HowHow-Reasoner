"""Fail-closed HARTH protocol-v2 preflight and execution command."""

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
EXPECTED_SUBJECTS = 22


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


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreflightFailure(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightFailure(f"cannot read JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightFailure(f"JSON input must be an object: {path}")
    return value


def validate_config(
    config: dict[str, Any], protocol: dict[str, Any], args: argparse.Namespace
) -> None:
    expected_classes = protocol.get("class_vocabulary")
    if getattr(args, "execute_real", False) and (
        not isinstance(expected_classes, list) or config.get("class_vocabulary") != expected_classes
    ):
        raise PreflightFailure("class vocabulary is not identical in frozen protocol and config")
    if getattr(args, "execute_real", False) and getattr(args, "classes", []) != expected_classes:
        raise PreflightFailure(
            "CLI class vocabulary does not equal frozen protocol/config vocabulary"
        )
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


def validate_output(output: Path, *, resume: bool = False) -> None:
    if output.exists() and not output.is_dir():
        raise PreflightFailure(f"output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()) and not resume:
        raise PreflightFailure(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)


CODE_PATHS = ("scripts/harth_v2_run.py", "src/howhow/episodes/harth/v2")


def _code_blobs() -> list[tuple[str, str]]:
    """Return committed source paths and blob IDs used by the runner."""
    listing = subprocess.run(
        ("git", "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *CODE_PATHS),
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    entries: list[tuple[str, str]] = []
    for item in listing.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        if kind != "blob" or mode == "160000":
            continue
        path = raw_path.decode("utf-8").replace("\\", "/")
        if not path.endswith(".py"):
            continue
        entries.append((path, object_id))
    if not entries:
        raise PreflightFailure("no committed HARTH runner source was found")
    return sorted(entries)


def code_hash() -> str:
    """Hash canonical Git identities, not checkout paths or working-tree bytes."""
    digest = hashlib.sha256()
    for path, blob_id in _code_blobs():
        path_bytes = path.encode("utf-8")
        blob_bytes = blob_id.encode("ascii")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(blob_bytes).to_bytes(8, "big"))
        digest.update(blob_bytes)
    return digest.hexdigest()


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
    protocol, config, archive_path = (
        args.protocol.resolve(),
        args.config.resolve(),
        args.archive.resolve(),
    )
    if git("status", "--porcelain"):
        raise PreflightFailure("working tree is dirty; refusing run preparation")
    if not protocol.is_file() or not config.is_file():
        raise PreflightFailure("frozen protocol or run config is missing")
    protocol_data, config_data = load_json(protocol), load_json(config)
    validate_config(config_data, protocol_data, args)
    archive = validate_archive(archive_path, config_data)
    output = args.output.resolve()
    validate_output(output, resume=args.execute_real and getattr(args, "resume", False))
    args.checkpoint = args.checkpoint.resolve()
    if args.checkpoint.parent != output:
        raise PreflightFailure("checkpoint must be inside the clean output directory")
    from howhow.episodes.harth.v2.run_guard import atomic_write

    manifest = build_manifest(args, protocol, config, archive)
    manifest = build_manifest(args, protocol, config, archive)
    manifest["duration_seconds"] = time.monotonic() - started
    path = output / "preflight.json"
    atomic_write(path, manifest)
    return path


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    value.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    value.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    value.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    value.add_argument("--checkpoint", type=Path, default=DEFAULT_OUTPUT / "checkpoint.json")
    value.add_argument("--class", dest="classes", action="append", default=[])
    value.add_argument("--seed", type=int, default=0)
    value.add_argument("--bootstrap-reps", type=int, default=BOOTSTRAP_REPS)
    value.add_argument("--max-outer-folds", type=int, default=22)
    value.add_argument("--wall-clock-minutes", type=int, default=30)
    value.add_argument("--preflight-only", action="store_true")
    value.add_argument("--execute-real", action="store_true")
    value.add_argument("--resume", action="store_true")
    return value


def execute_real(args: argparse.Namespace) -> Path:
    if os.environ.get(REAL_CONSENT_ENV) != "1":
        raise PreflightFailure(f"--execute-real requires {REAL_CONSENT_ENV}=1")
    from howhow.episodes.harth.v2 import protocol_hash
    from howhow.episodes.harth.v2.run_guard import RunGuard

    if len(args.classes) != 12:
        raise PreflightFailure("exactly 12 frozen classes must be supplied with --class")
    output = args.output.resolve()
    protocol_digest = protocol_hash(args.protocol)
    immutable_code = code_hash()
    guard = RunGuard(
        output,
        input_hash=sha256_file(args.archive),
        protocol_hash=protocol_digest,
        code_hash=immutable_code,
    )
    try:
        guard.stage("loader_start", hashes={"archive_sha256": sha256_file(args.archive)})
        return _execute_guarded(args, guard, protocol_digest, immutable_code)
    except BaseException as exc:
        guard.failure(exc, phase="loader_resume_engine_schema_generator")
        raise


def _execute_guarded(
    args: argparse.Namespace, guard: Any, protocol_digest: str, immutable_code: str
) -> Path:
    from howhow.episodes.harth.v2 import (
        engine_result_to_schema,
        input_hash,
        load_harth_archive,
        run_protocol,
        validate_result,
    )
    from howhow.episodes.harth.v2.run_guard import atomic_text_write, atomic_write

    source = load_harth_archive(
        args.archive, args.classes, protocol_hash=protocol_digest, code_hash=immutable_code
    )
    guard.stage("loader_complete", manifest=source.manifest)
    if len(source.manifest.get("subjects", [])) != EXPECTED_SUBJECTS:
        raise PreflightFailure("archive must contain exactly 22 eligible subjects")
    input_digest = input_hash(source.windows)
    guard.bind_input_hash(input_digest)
    identity = {
        "input_hash": input_digest,
        "protocol_hash": protocol_digest,
        "code_hash": immutable_code,
    }
    checkpoint = args.checkpoint
    guard.stage("resume_validation")
    if args.resume and checkpoint.is_file():
        saved = load_json(checkpoint)
        if any(saved.get(key) != value for key, value in identity.items()):
            raise PreflightFailure("checkpoint immutable identity mismatch")
        guard.restore_checkpoint(saved)
    guard.stage("engine_start", hashes=identity)

    def checkpoint_callback(payload: dict[str, Any]) -> None:
        guard.checkpoint(phase="engine_fold", hashes=identity, folds=payload["folds"])

    result = run_protocol(
        source.windows,
        args.classes,
        protocol_file=args.protocol,
        checkpoint=checkpoint,
        timeout_seconds=1800.0,
        code_hash=immutable_code,
        fold_callback=guard.record_fold,
        checkpoint_callback=checkpoint_callback,
    )
    guard.check_timeout()
    if result.status != "COMPLETE" or len(result.folds) != EXPECTED_SUBJECTS * 3:
        raise PreflightFailure(
            "engine did not produce all declared sensor configurations and folds"
        )
    guard.stage("schema_start", hashes=identity, folds=result.folds)
    artifact = validate_result(engine_result_to_schema(result, code_hash=immutable_code))
    target = args.output.resolve() / "results-v2.json"
    atomic_write(target, artifact)
    guard.stage("generator_start", hashes=identity, artifact=str(target))
    atomic_text_write(args.output.resolve() / "results.tex", _validated_tables(artifact))
    guard.check_timeout()
    guard.final(phase="complete", scientific_metrics=True, result_artifact=str(target))
    return target


def _validated_tables(artifact: dict[str, Any]) -> str:
    import importlib.util

    path = ROOT / "episodes/harth-calibration/paper/tools/generate_tables.py"
    spec = importlib.util.spec_from_file_location("harth_generate_tables", path)
    if spec is None or spec.loader is None:
        raise PreflightFailure("cannot load manuscript table generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.latex(artifact)


def main() -> int:
    args = parser().parse_args()
    if not args.preflight_only and not args.execute_real:
        print("refusing: choose --preflight-only or guarded --execute-real", file=sys.stderr)
        return 2
    try:
        path = preflight(args)
        if args.execute_real:
            path = execute_real(args)
            print(f"RUN PASS: {path}")
        else:
            print(f"PREFLIGHT PASS: {path}")
        return 0
    except BaseException as exc:
        output = args.output.resolve()
        if output.is_dir():
            failure = {
                "status": "FAILED",
                "scientific_metrics": False,
                "started_at_utc": utc_now(),
                "finished_at_utc": utc_now(),
                "argv": list(sys.argv),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "process": {"pid": os.getpid(), "executable": sys.executable},
            }
            from howhow.episodes.harth.v2.run_guard import atomic_write

            atomic_write(output / "failure.json", failure)
        print(f"RUN BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
