"""A deterministic, trusted-local subprocess provider.

This provider is deliberately honest: it constrains the child process by
argv/cwd/environment/time/output, but does not claim OS sandbox isolation.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from howhow.contracts import (
    ID,
    ProviderCapabilities,
    ProviderIdentity,
    ProviderReadiness,
    RecordStatus,
    RunManifest,
    TaskFailure,
    TaskResult,
    TaskSpec,
)


class DispatchState(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    OUTPUT_LIMIT = "output_limit"
    AMBIGUOUS = "ambiguous"


class OutputLimitExceeded(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerRequest:
    task: TaskSpec
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    timeout_seconds: float = 60.0
    output_limit_bytes: int = 1_048_576
    inherit_environment: bool = False
    cancellation: threading.Event | None = None
    code_revision: str = "unknown"
    seed: int | None = None

    def __post_init__(self) -> None:
        if not self.argv or any(not isinstance(part, str) or not part for part in self.argv):
            raise ValueError("argv must be a non-empty sequence of non-empty strings")
        if not self.cwd:
            raise ValueError("cwd is required")
        if self.timeout_seconds <= 0 or self.output_limit_bytes <= 0:
            raise ValueError("timeout and output limit must be positive")
        if any("=" in key or not key for key in self.env):
            raise ValueError("environment keys must be non-empty names")


@dataclass(frozen=True)
class RunnerResponse:
    task_result: TaskResult
    manifest: RunManifest
    stdout: bytes
    stderr: bytes
    state: DispatchState
    returncode: int | None


class LocalSubprocessProvider:
    """Runs an explicitly supplied command with trusted/degraded isolation."""

    def __init__(self, *, version: str = "0.1") -> None:
        self._identity = ProviderIdentity(
            provider_id="local-subprocess",
            provider_kind="runner",
            implementation="howhow.runners.local.LocalSubprocessProvider",
            version=version,
        )

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.identity,
            task_kinds=["subprocess"],
            concurrency=1,
            sandbox="trusted/degraded: no OS sandbox; explicit process bounds only",
            network="inherited-by-command-policy; provider does not claim network isolation",
        )

    def readiness(self, *, checked_at: datetime | None = None) -> ProviderReadiness:
        now = checked_at or datetime.now(UTC)
        status = "READY_TRUSTED_DEGRADED" if os.name == "nt" else "READY_TRUSTED_DEGRADED"
        return ProviderReadiness(provider=self.identity, status=status, checked_at=now)

    def run(self, request: RunnerRequest) -> RunnerResponse:
        if not isinstance(request, RunnerRequest):
            raise TypeError("request must be RunnerRequest")
        started = datetime.now(UTC)
        environment = dict(request.env)
        if request.inherit_environment:
            inherited = dict(os.environ)
            inherited.update(environment)
            environment = inherited
        process = subprocess.Popen(
            list(request.argv),
            cwd=request.cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        try:
            stdout, stderr = self._communicate(process, request)
            state = DispatchState.COMPLETED if process.returncode == 0 else DispatchState.FAILED
        except RunCancelled:
            process.kill()
            stdout, stderr = process.communicate()
            state = DispatchState.CANCELLED
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            state = DispatchState.TIMED_OUT
        except OutputLimitExceeded:
            process.kill()
            stdout, stderr = process.communicate()
            state = DispatchState.OUTPUT_LIMIT
        except Exception:
            process.kill()
            process.communicate()
            raise
        status = (
            RecordStatus.SUCCEEDED
            if state == DispatchState.COMPLETED
            else (
                RecordStatus.CANCELLED if state == DispatchState.CANCELLED else RecordStatus.FAILED
            )
        )
        failure = (
            None
            if status == RecordStatus.SUCCEEDED
            else TaskFailure(
                code=state.value,
                message=f"local subprocess ended with {state.value}",
                retryable=state in {DispatchState.TIMED_OUT},
            )
        )
        result = TaskResult(
            task_id=request.task.task_id,
            attempt=1,
            status=status,
            provider=self.identity,
            failure=failure,
            occurred_at=datetime.now(UTC),
        )
        manifest = RunManifest(
            run_id=ID(value=f"{request.task.task_id.value}/run-1"),
            task_id=request.task.task_id,
            code_revision=request.code_revision,
            command=list(request.argv),
            seed=request.seed,
            status=status,
            completed=state != DispatchState.AMBIGUOUS,
            occurred_at=started,
        )
        return RunnerResponse(result, manifest, stdout, stderr, state, process.returncode)

    def _communicate(
        self, process: subprocess.Popen[bytes], request: RunnerRequest
    ) -> tuple[bytes, bytes]:
        deadline = time.monotonic() + request.timeout_seconds
        while True:
            if request.cancellation and request.cancellation.is_set():
                raise RunCancelled("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(request.argv, request.timeout_seconds)
            try:
                stdout, stderr = process.communicate(timeout=min(remaining, 0.05))
                break
            except subprocess.TimeoutExpired:
                continue
        if len(stdout) > request.output_limit_bytes or len(stderr) > request.output_limit_bytes:
            raise OutputLimitExceeded("stdout or stderr exceeded output limit")
        return stdout, stderr
