"""Deterministic provider capability registry for the control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from howhow.runners.local import LocalSubprocessProvider


class ProviderRegistry:
    def __init__(self, providers: list[Any] | None = None) -> None:
        self.providers = providers or [LocalSubprocessProvider()]

    def capabilities(self) -> list[Any]:
        return [provider.capabilities() for provider in self.providers]

    def readiness(self) -> list[Any]:
        return [provider.readiness(checked_at=datetime.now(UTC)) for provider in self.providers]

    def get(self, provider_id: str) -> Any:
        for provider in self.providers:
            if provider.identity.provider_id == provider_id:
                return provider
        raise KeyError(provider_id)
