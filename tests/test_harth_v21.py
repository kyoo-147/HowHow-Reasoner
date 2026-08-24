from __future__ import annotations

import pytest

from howhow.episodes.harth.v21 import (
    V21Error,
    atomic_canonical_write,
    bootstrap,
    canonical_bytes,
    ece_spec_hash,
    f1_report,
    generate_outputs,
    holm,
    migration_v2_to_v21,
    pairing_manifest,
    pvalue,
    subject_macro,
    subject_metrics,
    support_gate,
    transition,
    validate_result,
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
    held_out = support_gate(["c0"], CLASSES, stage="held_out_test", minimum=2)
    assert held_out["status"] == "OUTER_TEST_OBSERVED"
    assert held_out["class_status"]["c1"]["status"] == "NOT_ESTIMABLE"
    assert held_out["aggregate_metrics_allowed"] is True
    assert (
        support_gate(["c0"], CLASSES, stage="training", minimum=2)["reason"]
        == "TRAINING_CLASS_SUPPORT"
    )
    assert (
        subject_macro(
            {"s": {"n": 0}},
            "nll",
            frozen_subjects=["s"],
            exclusion_manifest=[{"subject_id": "s", "reason": "ZERO_WINDOWS"}],
        )["reason"]
        == "NO_ELIGIBLE_SUBJECTS"
    )


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
    report = migration_v2_to_v21({"schema_version": "harth-result-v1"})
    assert report["status"] == "REJECTED"


def test_canonical_json_is_utf8_sorted_with_lf():
    assert canonical_bytes({"é": 1, "a": [2]}) == '{"a":[2],"é":1}\n'.encode()


def test_pairing_manifest_rejects_duplicate_unpaired_and_window_tamper():
    base = {
        "subject_id": "s1",
        "contrast_id": "calibrated_vs_uncalibrated",
        "estimand_id": "nll",
        "reason": "",
        "window_set_hash": "a" * 64,
        "population_rule_id": "frozen",
    }
    with pytest.raises(V21Error, match="UNPAIRED"):
        pairing_manifest([{**base, "arm": "calibrated"}])
    with pytest.raises(V21Error, match="DUPLICATE"):
        pairing_manifest(
            [
                {**base, "arm": "calibrated"},
                {**base, "arm": "calibrated"},
                {**base, "arm": "uncalibrated"},
            ]
        )
    with pytest.raises(V21Error, match="WINDOW"):
        pairing_manifest(
            [
                {**base, "arm": "calibrated"},
                {**base, "arm": "uncalibrated", "window_set_hash": "b" * 64},
            ]
        )


def test_empty_and_malformed_jobs_fail_closed():
    with pytest.raises(V21Error):
        bootstrap({}, job_id="")
    with pytest.raises(V21Error, match="JOB_ID"):
        bootstrap({"s": 1.0}, job_id="arbitrary")
    with pytest.raises(V21Error):
        pvalue({}, estimand="NLL")
    with pytest.raises(V21Error):
        holm({"H_NLL": -0.1, "H_BRIER": 0.1, "H_ECE": 0.1})


def test_strict_result_validator_rejects_unknowns_and_bad_bindings():
    hashes = {
        name: "a" * 64
        for name in (
            "protocol_sha256",
            "schema_sha256",
            "config_sha256",
            "code_sha256",
            "input_sha256",
            "vocabulary_sha256",
            "eligibility_manifest_sha256",
            "pairing_manifest_sha256",
        )
    }
    result = {
        "schema_version": "result-schema-v2.1",
        "protocol_version": "protocol-v2.1",
        "status": "NOT_ESTIMABLE",
        "state": "NOT_ESTIMABLE",
        "scope": "estimand",
        "hashes": hashes,
        "support": {"training": {}, "inner_calibration": {}, "held_out_test": {}},
        "estimability": {"nll": {}, "brier": {}, "ece": {}},
        "population": {
            "frozen_subject_ids": [],
            "exclusions": [],
            "population_rule_id": "frozen",
            "eligibility_manifest_hash": "a" * 64,
        },
        "pairing": {
            "pairing_manifest_hash": "a" * 64,
            "eligible_subject_hash": "a" * 64,
            "records": [],
        },
        "family": {
            "family_id": "v2-primary-calibrated-vs-uncalibrated-3",
            "hypotheses": [],
            "alpha": 0.05,
            "m": 3,
            "status": "INCOMPLETE_FAMILY",
        },
        "outputs": {"generator": "", "manuscript": ""},
        "claim_boundary": "synthetic_structural_only_no_performance_claim",
    }
    assert validate_result(result)["state"] == "NOT_ESTIMABLE"
    with pytest.raises(V21Error, match="UNKNOWN"):
        validate_result({**result, "unexpected": True})


def test_migration_refuses_metric_fields_but_emits_loss_report():
    report = migration_v2_to_v21(
        {"schema_version": "harth-result-v2", "folds": [{"metrics": {"nll": 1.0}}]}
    )
    assert report["status"] == "REJECTED"
    assert "folds[0].metrics" in report["metric_fields_refused"]
    assert "folds[0].metrics.nll" in report["metric_fields_refused"]


def test_generator_is_truthful_and_atomic_publication_is_exclusive(tmp_path):
    output = generate_outputs({"status": "NOT_ESTIMABLE"})
    assert "No performance claim" in output["manuscript.md"]
    path = tmp_path / "result.json"
    atomic_canonical_write(path, {"b": 2, "a": 1})
    with pytest.raises(V21Error, match="IMMUTABLE"):
        atomic_canonical_write(path, {"a": 9})
