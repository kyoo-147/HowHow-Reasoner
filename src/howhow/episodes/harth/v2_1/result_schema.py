"""Versioned result-schema-v2.1 public surface."""

from ..v21 import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    NotEstimable,
    V21Error,
    atomic_canonical_write,
    canonical_bytes,
    canonical_hash,
    migration_v2_to_v21,
)

ResultSchemaError = V21Error
__all__ = [
    "SCHEMA_VERSION",
    "PROTOCOL_VERSION",
    "ResultSchemaError",
    "NotEstimable",
    "atomic_canonical_write",
    "canonical_bytes",
    "canonical_hash",
    "migration_v2_to_v21",
]
