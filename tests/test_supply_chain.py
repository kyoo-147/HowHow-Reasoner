from __future__ import annotations

from scripts.audit_supply_chain import audit, locked_packages


def test_locked_inventory_has_both_ecosystems_and_harth_attribution() -> None:
    packages = locked_packages()
    assert any(package["ecosystem"] == "pypi" for package in packages)
    assert any(package["ecosystem"] == "npm" for package in packages)
    report, _ = audit(refresh=False)
    assert report["harth"] == {
        "license": "CC BY 4.0",
        "attribution_verified": True,
        "manifest": "episodes/harth-calibration/data/manifest.json",
    }


def test_audit_never_reports_secret_content() -> None:
    report, findings = audit(refresh=False)
    output = str(report) + str(findings)
    assert "BEGIN " + "PRIVATE KEY" not in output
    assert "AK" + "IA" not in output
