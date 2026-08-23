"""Provider protocol shared by bounded runners."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from howhow.contracts import ProviderCapabilities, ProviderIdentity, ProviderReadiness
from howhow.runners.local import RunnerRequest, RunnerResponse


class RunnerError(RuntimeError):
    """A typed, non-successful runner operation."""


class Provider(Protocol):
    @property
    def identity(self) -> ProviderIdentity: ...

    def readiness(self, *, checked_at: datetime | None = None) -> ProviderReadiness: ...

    def capabilities(self) -> ProviderCapabilities: ...

    def run(self, request: RunnerRequest) -> RunnerResponse: ...
