"""Atomic, metrics-free run artifacts and failure preservation for HARTH v2."""

from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

MAX_OUTER_FOLDS = 22
RUN_TIMEOUT_SECONDS = 1800.0
T = TypeVar("T")


class RunGuardFailure(RuntimeError):
    """A run guard invariant failed."""


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _atomic(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write(path: str | Path, value: Mapping[str, Any]) -> None:
    """Write JSON durably, then atomically replace the target."""
    _atomic(path, _json(value))


def atomic_text_write(path: str | Path, value: str) -> None:
    """Write text durably, then atomically replace the target."""
    _atomic(path, value)


class RunGuard:
    """Own every validation, execution, final, and failure artifact."""

    def __init__(
        self,
        output: str | Path,
        *,
        input_hash: str,
        protocol_hash: str,
        code_hash: str | None = None,
        timeout_seconds: float = RUN_TIMEOUT_SECONDS,
    ) -> None:
        self.output = Path(output)
        self.input_hash = input_hash
        self.protocol_hash = protocol_hash
        self.code_hash = code_hash
        if timeout_seconds != RUN_TIMEOUT_SECONDS:
            raise RunGuardFailure("run timeout is fixed at 1800 seconds")
        self.timeout_seconds = RUN_TIMEOUT_SECONDS
        self.started = time.monotonic()
        self.completed_folds: list[Any] = []
        self.output.mkdir(parents=True, exist_ok=True)

    def bind_input_hash(self, input_hash: str) -> None:
        if self.completed_folds:
            raise RunGuardFailure("cannot change input identity after execution starts")
        self.input_hash = input_hash

    def _base(self) -> dict[str, Any]:
        return {
            "scientific_metrics": False,
            "input_hash": self.input_hash,
            "protocol_hash": self.protocol_hash,
            "code_hash": self.code_hash,
            "completed_folds": self.completed_folds,
        }

    def checkpoint(
        self, *, phase: str, hashes: Mapping[str, str] | None = None, **extra: Any
    ) -> Path:
        payload = (
            self._base()
            | {
                "status": "RUNNING",
                "phase": phase,
                "hashes": dict(hashes or {}),
                "updated_at": time.time(),
            }
            | extra
        )
        target = self.output / "checkpoint.json"
        atomic_write(target, payload)
        return target

    def final(self, *, phase: str = "complete", **extra: Any) -> Path:
        payload = (
            self._base()
            | {
                "status": "COMPLETE",
                "phase": phase,
                "finished_at": time.time(),
            }
            | extra
        )
        target = self.output / "final.json"
        atomic_write(target, payload)
        return target

    def failure(
        self, exc: BaseException, *, phase: str, reason: str | None = None, **extra: Any
    ) -> Path:
        payload = (
            self._base()
            | {
                "status": "FAILED",
                "phase": phase,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "reason": reason or str(exc),
                "traceback": "".join(traceback.format_exception(exc)),
                "finished_at": time.time(),
            }
            | extra
        )
        target = self.output / "failure.json"
        atomic_write(target, payload)
        return target

    def check_timeout(self) -> None:
        if time.monotonic() - self.started >= self.timeout_seconds:
            raise TimeoutError("run exceeded fixed 1800s timeout")

    def record_fold(self, fold: Any) -> None:
        if len(self.completed_folds) >= MAX_OUTER_FOLDS:
            raise RunGuardFailure("maximum 22 outer folds exceeded")
        self.completed_folds.append(fold)

    def execute(self, operation: Callable[[], T], *, phase: str = "execution") -> T:
        try:
            value = operation()
            self.final(phase=phase)
            return value
        except BaseException as exc:
            self.failure(exc, phase=phase)
            raise


def write_checkpoint(path: str | Path, payload: Mapping[str, Any]) -> None:
    atomic_write(path, {**payload, "scientific_metrics": False})


def write_final(path: str | Path, payload: Mapping[str, Any]) -> None:
    atomic_write(path, {**payload, "scientific_metrics": False, "status": "COMPLETE"})


def write_failure(
    path: str | Path,
    exc: BaseException,
    *,
    phase: str,
    completed_folds: list[Any] | None = None,
    **context: Any,
) -> None:
    atomic_write(
        path,
        {
            "scientific_metrics": False,
            "status": "FAILED",
            "phase": phase,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": "".join(traceback.format_exception(exc)),
            "completed_folds": completed_folds or [],
            **context,
        },
    )
