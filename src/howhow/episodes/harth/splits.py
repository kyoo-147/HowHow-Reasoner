"""Frozen subject-held-out split and leakage guards."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SubjectSplit:
    train_subjects: tuple[str, ...]
    test_subjects: tuple[str, ...]
    seed: int
    frozen: bool = True


def subject_held_out_split(
    subjects: Sequence[str], test_subjects: Sequence[str], *, seed: int = 0
) -> SubjectSplit:
    """Create a split from subject IDs; no row-level random split is permitted."""
    all_subjects = tuple(dict.fromkeys(str(s) for s in subjects))
    held_out = tuple(dict.fromkeys(str(s) for s in test_subjects))
    if not all_subjects or not held_out or not set(held_out).issubset(all_subjects):
        raise ValueError("test subjects must be non-empty and drawn from subjects")
    train = tuple(s for s in all_subjects if s not in held_out)
    if not train:
        raise ValueError("at least one training subject is required")
    return SubjectSplit(train, held_out, seed)


def assert_no_subject_leakage(train_subjects: Sequence[str], test_subjects: Sequence[str]) -> None:
    overlap = set(map(str, train_subjects)) & set(map(str, test_subjects))
    if overlap:
        raise ValueError(f"subject leakage between train and test: {sorted(overlap)}")
