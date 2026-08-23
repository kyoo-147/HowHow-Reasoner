from __future__ import annotations

import sys
import threading
import time
from datetime import UTC, datetime

import pytest

from howhow.contracts import ID, BudgetReservation, ProviderIdentity, TaskSpec
from howhow.orchestration import BudgetExceeded, BudgetLedger, LeaseManager, Orchestrator
from howhow.runners import LocalSubprocessProvider, RunnerRequest

NOW = datetime.now(UTC)


def task(key: str = "key") -> TaskSpec:
    provider = ProviderIdentity(
        provider_id="local-subprocess", provider_kind="runner", implementation="test", version="1"
    )
    return TaskSpec(
        task_id=ID(value="task-1"),
        project_id=ID(value="project"),
        objective="test",
        provider=provider,
        idempotency_key=key,
        occurred_at=NOW,
    )


def request(key: str = "key", **kwargs: object) -> RunnerRequest:
    return RunnerRequest(
        task=task(key), argv=(sys.executable, "-c", "print('ok')"), cwd=".", env={}, **kwargs
    )


def test_explicit_environment_is_redacted_and_output_captured() -> None:
    req = RunnerRequest(
        task=task(),
        argv=(sys.executable, "-c", "import os; print(os.getenv('HOWHOW_SECRET', 'missing'))"),
        cwd=".",
        env={},
    )
    result = LocalSubprocessProvider().run(req)
    assert result.stdout.strip() == b"missing"
    assert result.manifest.command[0] == sys.executable


def test_timeout_preserves_failed_run() -> None:
    req = RunnerRequest(
        task=task(),
        argv=(sys.executable, "-c", "import time; time.sleep(2)"),
        cwd=".",
        env={},
        timeout_seconds=0.05,
    )
    result = LocalSubprocessProvider().run(req)
    assert result.state.value == "timed_out"
    assert result.manifest.completed is True
    assert result.task_result.failure is not None


def test_output_limit_preserves_failure() -> None:
    req = RunnerRequest(
        task=task(),
        argv=(sys.executable, "-c", "print('x' * 100)"),
        cwd=".",
        env={},
        output_limit_bytes=10,
    )
    result = LocalSubprocessProvider().run(req)
    assert result.state.value == "output_limit"
    assert result.task_result.failure is not None


def test_budget_and_duplicate_idempotency() -> None:
    ledger = BudgetLedger(BudgetReservation(reservation_id=ID(value="b"), resource="runs", limit=1))
    orchestrator = Orchestrator(LocalSubprocessProvider(), ledger)
    first = orchestrator.dispatch(request(), cost_reservation=1)
    second = orchestrator.dispatch(request(), cost_reservation=1)
    assert first == second
    with pytest.raises(BudgetExceeded):
        ledger.reserve(1)


def test_stale_fencing_rejects_result() -> None:
    leases = LeaseManager()
    lease = leases.acquire(task(), task().provider, ttl_seconds=0.01)
    time.sleep(0.02)
    assert not leases.validate(lease)


def test_cancellation_event_is_typed() -> None:
    event = threading.Event()
    event.set()
    req = RunnerRequest(
        task=task(),
        argv=(sys.executable, "-c", "print('late')"),
        cwd=".",
        env={},
        cancellation=event,
    )
    result = LocalSubprocessProvider().run(req)
    assert result.state.value in {"cancelled", "output_limit"}


def test_native_provider_is_truthfully_serial_degraded() -> None:
    capabilities = LocalSubprocessProvider().capabilities()
    assert capabilities.concurrency == 1
    assert "degraded" in capabilities.sandbox
    assert "Docker" not in capabilities.sandbox
