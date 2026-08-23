"""In-memory control-plane primitives with explicit safety semantics.

These primitives are intentionally provider-neutral and serializable at the
call boundary. A later filesystem/SQLite store can implement the same rules.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from howhow.contracts import (
    ID,
    BudgetReservation,
    LeaseRecord,
    ProviderIdentity,
    TaskSpec,
)
from howhow.runners.local import RunnerRequest, RunnerResponse
from howhow.runners.protocol import Provider


class BudgetExceeded(RuntimeError):
    pass


class AmbiguousDispatch(RuntimeError):
    """Dispatch outcome is unknown; retrying would risk duplicate side effects."""


@dataclass(frozen=True)
class Lease:
    record: LeaseRecord
    owner_epoch: int


class BudgetLedger:
    def __init__(self, reservation: BudgetReservation) -> None:
        self.reservation = reservation
        self._spent = reservation.spent
        self._reserved = 0.0
        self._lock = Lock()

    @property
    def available(self) -> float:
        return self.reservation.limit - self._spent - self._reserved

    def reserve(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        with self._lock:
            if amount > self.available:
                raise BudgetExceeded("budget reservation would exceed hard limit")
            self._reserved += amount

    def charge(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        with self._lock:
            if amount > self._reserved:
                raise BudgetExceeded("charge exceeds reserved amount")
            self._reserved -= amount
            self._spent += amount

    def release(self, amount: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - amount)


class LeaseManager:
    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}
        self._epochs: dict[str, int] = {}
        self._lock = Lock()

    def acquire(
        self,
        task: TaskSpec,
        provider: ProviderIdentity,
        *,
        attempt: int = 1,
        ttl_seconds: float = 60.0,
    ) -> Lease:
        if ttl_seconds <= 0:
            raise ValueError("ttl must be positive")
        with self._lock:
            key = task.task_id.value
            current = self._leases.get(key)
            now = datetime.now(UTC)
            if current and current.record.expires_at > now:
                raise RuntimeError("task already leased")
            epoch = self._epochs.get(key, 0) + 1
            self._epochs[key] = epoch
            record = LeaseRecord(
                lease_id=ID(value=f"{key}/lease-{epoch}"),
                task_id=task.task_id,
                provider=provider,
                attempt=attempt,
                fencing_token=secrets.token_hex(16),
                expires_at=now + timedelta(seconds=ttl_seconds),
                occurred_at=now,
            )
            lease = Lease(record, epoch)
            self._leases[key] = lease
            return lease

    def validate(self, lease: Lease) -> bool:
        with self._lock:
            current = self._leases.get(lease.record.task_id.value)
            return bool(
                current
                and current.record.fencing_token == lease.record.fencing_token
                and current.owner_epoch == lease.owner_epoch
                and current.record.expires_at > datetime.now(UTC)
            )

    def release(self, lease: Lease) -> None:
        with self._lock:
            if self._leases.get(lease.record.task_id.value) == lease:
                del self._leases[lease.record.task_id.value]


@dataclass(frozen=True)
class DispatchReceipt:
    key: str
    response: RunnerResponse | None
    state: str
    reconcile_required: bool = False


class Orchestrator:
    def __init__(
        self, provider: Provider, budget: BudgetLedger, leases: LeaseManager | None = None
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.leases = leases or LeaseManager()
        self._dispatches: dict[str, DispatchReceipt] = {}
        self._cancelled: set[str] = set()
        self._lock = Lock()

    def dispatch(
        self,
        request: RunnerRequest,
        *,
        cost_reservation: float = 1.0,
        dispatch: Callable[[RunnerRequest], RunnerResponse] | None = None,
    ) -> DispatchReceipt:
        key = request.task.idempotency_key
        with self._lock:
            prior = self._dispatches.get(key)
            if prior:
                return prior
            self._dispatches[key] = DispatchReceipt(key, None, "reserved")
        self.budget.reserve(cost_reservation)
        lease = self.leases.acquire(request.task, request.task.provider)
        try:
            response = (dispatch or self.provider.run)(request)
        except Exception as exc:
            # The provider may have started a side effect before reporting failure.
            receipt = DispatchReceipt(key, None, "reconcile_required", True)
            with self._lock:
                self._dispatches[key] = receipt
            self.leases.release(lease)
            raise AmbiguousDispatch(str(exc)) from exc
        if not self.leases.validate(lease):
            receipt = DispatchReceipt(key, response, "stale_fencing", True)
        else:
            self.budget.charge(cost_reservation)
            receipt = DispatchReceipt(key, response, response.state.value)
        with self._lock:
            self._dispatches[key] = receipt
        self.leases.release(lease)
        return receipt

    def cancel(self, idempotency_key: str) -> None:
        self._cancelled.add(idempotency_key)

    def reconcile(self, idempotency_key: str) -> DispatchReceipt:
        receipt = self._dispatches.get(idempotency_key)
        if receipt is None:
            raise KeyError(idempotency_key)
        return receipt
