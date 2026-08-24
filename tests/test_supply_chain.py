from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.audit_supply_chain import audit, locked_packages


def test_locked_inventory_has_both_ecosystems_and_harth_attribution() -> None:
    packages = locked_packages()
    assert any(package["ecosystem"] == "pypi" for package in packages)
    assert any(package["ecosystem"] == "npm" for package in packages)
    assert all(package["name"] and package["version"] for package in packages)
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


def test_vulnerability_scan_status_is_structured() -> None:
    report, _ = audit(refresh=False)
    assert report["vulnerability_scan"]["status"] == "NOT_APPLICABLE"
    assert report["vulnerability_scan"]["schema"].endswith("vulnerability-scan.v1")


def test_public_compliance_artifacts_reject_private_paths() -> None:
    root = Path(__file__).parents[1] / "compliance"
    private_path = re.compile(
        r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home|tmp)/|(?:\.treehouse|\.venv|worktree))",
        re.IGNORECASE,
    )
    for path in (*root.glob("*.json"), *root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert not private_path.search(text), path
        if path.suffix == ".json":
            json.loads(text)


def test_artifact_provenance_is_lock_derived_and_platform_scoped() -> None:
    from scripts.audit_supply_chain import locked_artifacts

    artifacts = locked_artifacts()
    numpy = [item for item in artifacts if item["package"] == "numpy"]
    assert any(item["scope"] == "sdist" for item in numpy)
    windows = [item for item in numpy if "win_amd64" in item["artifact"]]
    assert (
        windows[0]["sha256"] == "9e196ade2400c0c737d93465327d1ae7c06c7cb8a1756121ebf54b06ca183c7f"
    )
    assert windows[0]["upstream_notices"] == "LICENSE.txt,LICENSE_win32.txt"
    assert all("Users" not in str(item) and ".venv" not in str(item) for item in artifacts)


def test_numpy_license_summary_is_single_line_and_howhow_stays_unknown() -> None:
    report, _ = audit(refresh=False)
    numpy = next(item for item in report["packages"] if item["name"] == "numpy")
    howhow = next(item for item in report["packages"] if item["name"] == "howhow")
    assert "\n" not in numpy["license"]
    assert howhow["license"] == "UNKNOWN"
    assert howhow["status"] == "UNKNOWN"
    assert (
        "artifacts" in report
        and report["upstream_notices"]["LICENSE_win32.txt"]["scope"] == "wheel-platform:windows"
    )
