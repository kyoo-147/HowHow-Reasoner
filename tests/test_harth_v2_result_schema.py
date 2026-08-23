# ruff: noqa: E501
from __future__ import annotations

import copy
import math

import pytest

from howhow.episodes.harth.v2 import (
    ResultSchemaError,
    Window,
    engine_result_to_schema,
    run_protocol,
    validate_result,
)


def fixture_windows() -> list[Window]:
    return [
        Window(
            subject,
            "session-a",
            "rest" if index < 3 else "walk",
            (offset + (0 if index < 3 else 2),) * 12,
            f"{subject}-{index}",
        )
        for subject, offset in (("S01", 0.0), ("S02", 4.0), ("S03", 8.0))
        for index in range(6)
    ]


def artifact() -> dict[str, object]:
    result = run_protocol(fixture_windows(), ["rest", "walk"])
    return engine_result_to_schema(result, code_hash="a" * 64)


def test_engine_handoff_is_complete_and_versioned() -> None:
    value = validate_result(artifact())
    assert value["schema_version"] == "harth-result-v1"
    assert len(value["fold_ids"]) == 9
    assert set(value["gates"]) == {
        "provenance",
        "finite_metrics",
        "fold_integrity",
        "class_support",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["folds"][0].update(temperature=True),
        lambda x: x["folds"].pop(),
    ],
)
def test_schema_rejects_structural_mutations(mutation) -> None:
    value = copy.deepcopy(artifact())
    mutation(value)
    with pytest.raises(ResultSchemaError):
        validate_result(value)


def test_schema_rejects_bool_nan_and_gate_lies() -> None:
    value = artifact()
    value["folds"][0]["states"]["calibrated"]["metrics"]["nll"] = True
    with pytest.raises(ResultSchemaError):
        validate_result(value)
    value = artifact()
    value["folds"][0]["states"]["calibrated"]["metrics"]["nll"] = math.nan
    with pytest.raises(ResultSchemaError):
        validate_result(value)
    value = artifact()
    value["gates"] = {key: False for key in value["gates"]}
    with pytest.raises(ResultSchemaError):
        validate_result(value)
