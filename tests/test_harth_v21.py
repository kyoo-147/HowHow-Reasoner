from __future__ import annotations

import pytest

from howhow.episodes.harth.v21 import (
    V21Error,
    bootstrap,
    canonical_bytes,
    ece_spec_hash,
    f1_report,
    holm,
    migration_v2_to_v21,
    pvalue,
    subject_macro,
    subject_metrics,
    support_gate,
    transition,
)

CLASSES = [f"c{i}" for i in range(12)]


def probs():
    rows = []
    for i in range(12):
        row = [0.0] * 12
        row[i] = 1.0
        rows.append(row)
    return rows


def test_exact_subject_metrics_and_zero_probability_floor():
    result = subject_metrics(CLASSES, probs(), CLASSES)
    assert result["nll"] == pytest.approx(0.0)
    assert result["brier"] == pytest.approx(0.0)
    assert result["ece"] == pytest.approx(0.0)
    assert result["p_floor"] == 1e-12
    assert len(result["ece_bins"]) == 10
    assert ece_spec_hash() == ece_spec_hash()


def test_probability_domain_does_not_normalize():
    bad = probs()
    bad[0][0] = 1.1
    with pytest.raises(V21Error, match="PROBABILITY_DOMAIN"):
        subject_metrics(CLASSES, bad, CLASSES)


def test_support_and_subject_macro_statuses():
    assert support_gate(["c0"], CLASSES, stage="held_out_test", minimum=2)["status"] == "PASS"
    assert (
        support_gate(["c0"], CLASSES, stage="training", minimum=2)["reason"]
        == "TRAINING_CLASS_SUPPORT"
    )
    assert subject_macro({"s": {"n": 0}}, "nll")["reason"] == "NO_ELIGIBLE_SUBJECTS"


def test_f1_observed_support_and_fixed_vocab():
    report = f1_report(
        CLASSES[:2] + ["c0"], [[1, 0] + [0] * 10, [0, 1] + [0] * 10, [1, 0] + [0] * 10], CLASSES
    )
    assert report["K_obs"] == ["c0", "c1"]
    assert report["observed_macro_f1"]["denominator"] == 2
    assert report["fixed_vocabulary_macro_f1"]["status"] == "NOT_ESTIMABLE"


def test_bootstrap_job_and_frozen_quantiles_are_deterministic():
    job = "protocol-v2.1|bootstrap|subject_macro|nll|single_arm|full_sensor|calibrated|seed=0"
    first = bootstrap({str(i): float(i) for i in range(20)}, job_id=job)
    assert first == bootstrap({str(i): float(i) for i in range(20)}, job_id=job)
    assert first["generator"] == "PCG64"
    assert first["valid_replicates"] == 2000


def test_pvalue_threshold_and_holm_incomplete_family():
    assert pvalue({str(i): -0.1 for i in range(19)}, estimand="NLL")["status"] == "NOT_ESTIMABLE"
    result = holm({"H_NLL": 0.01, "H_BRIER": 0.02, "H_ECE": 0.03})
    assert result["status"] == "COMPLETE"
    assert [x["identifier"] for x in result["hypotheses"]] == ["H_NLL", "H_BRIER", "H_ECE"]
    assert holm({"H_NLL": 0.1})["status"] == "INCOMPLETE_FAMILY"


def test_state_machine_and_loss_aware_migration():
    assert transition("DECLARED", "PREFLIGHT_PASS")["state"] == "PREFLIGHT_PASS"
    with pytest.raises(V21Error):
        transition("DECLARED", "LOADED")
    with pytest.raises(V21Error, match="REJECT_V2"):
        migration_v2_to_v21({"schema_version": "harth-result-v1"})


def test_canonical_json_is_utf8_sorted_with_lf():
    assert canonical_bytes({"é": 1, "a": [2]}) == '{"a":[2],"é":1}\n'.encode()
