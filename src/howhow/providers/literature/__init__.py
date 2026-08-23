"""Read-only literature provider adapters."""

from .adapters import PROVIDERS, LiteratureAdapter, Paper, ProviderConfig

__all__ = ["LiteratureAdapter", "Paper", "ProviderConfig", "PROVIDERS"]
