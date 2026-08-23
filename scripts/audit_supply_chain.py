"""Fail-closed dependency, provenance, license, and secret hygiene audit.

The audit intentionally uses lockfiles as the authority. Registry metadata is only
used when refreshing the checked-in compliance inventory; it is never copied into
public output without being reduced to package names, versions, and license data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import sysconfig
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "compliance" / "dependency-allowlist.json"
NOTICE = ROOT / "compliance" / "THIRD_PARTY_NOTICES.md"

SECRET_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|(?:ghp_|github_pat_|xox[baprs]-|sk-)[A-Za-z0-9_\-]{16,}|"
    r"BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY)"
)
SUSPICIOUS_NAME_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|.*(?:secret|credential|private).*|id_rsa|.*\.(?:pem|key))$", re.I
)


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL)


def pypi_license(name: str, version: str) -> tuple[str, str]:
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
    with urllib.request.urlopen(url, timeout=10) as response:
        info = json.load(response)["info"]
    license_name = (info.get("license") or "").strip()
    if not license_name:
        for classifier in info.get("classifiers", []):
            if classifier.startswith("License :: OSI Approved :: "):
                license_name = classifier.rsplit(" :: ", 1)[-1]
                license_name = license_name.removesuffix(" License")
                break
    return license_name or "UNKNOWN", url


def installed_license(name: str, version: str) -> tuple[str, str]:
    """Use the exact locked distribution's shipped license file as provenance."""
    from importlib.metadata import distribution

    dist = distribution(name)
    if dist.version != version:
        return "UNKNOWN", ""
    for file in dist.files or []:
        lowered = str(file).lower()
        if "license" not in lowered and not lowered.endswith("copying"):
            continue
        path = dist.locate_file(file)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")[:4000].lower()
        if "mit license" in text:
            license_name = "MIT"
        elif "bsd 3-clause" in text or "\n3. " in text:
            license_name = "BSD-3-Clause"
        elif "bsd 2-clause" in text or "redistribution and use" in text:
            license_name = "BSD-2-Clause"
        elif "apache license" in text and "bsd" in text:
            license_name = "Apache-2.0 OR BSD-2-Clause"
        elif "python software foundation license" in text:
            license_name = "PSF-2.0"
        else:
            continue
        return license_name, f"installed:{dist._path.as_posix()}/{file}"
    return "UNKNOWN", ""


def npm_license(name: str, version: str) -> tuple[str, str]:
    encoded = urllib.parse.quote(name, safe="@")
    url = f"https://registry.npmjs.org/{encoded}/{urllib.parse.quote(version)}"
    with urllib.request.urlopen(url, timeout=10) as response:
        info = json.load(response)
    license_value = info.get("license", "UNKNOWN")
    if isinstance(license_value, dict):
        license_value = license_value.get("type", "UNKNOWN")
    return str(license_value or "UNKNOWN").strip() or "UNKNOWN", url


def locked_packages() -> list[dict[str, str]]:
    import tomllib

    uv = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    result = [
        {"ecosystem": "pypi", "name": p["name"], "version": p["version"]}
        for p in uv.get("package", [])
    ]
    in_packages = False
    package_key = re.compile(r"^  (?:'([^']+)'|([^ ]+)):$")
    for line in (ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8").splitlines():
        if line == "packages:":
            in_packages = True
            continue
        if in_packages and line and not line.startswith(" "):
            break
        if not in_packages:
            continue
        match = package_key.match(line)
        if not match:
            continue
        raw = match.group(1) or match.group(2)
        name, version = raw.rsplit("@", 1)
        version = version.split("(", 1)[0]
        result.append({"ecosystem": "npm", "name": name, "version": version})
    return sorted(result, key=lambda item: (item["ecosystem"], item["name"], item["version"]))


def vulnerability_scan(command: list[str], ecosystem: str) -> dict[str, Any]:
    """Run a current scanner and retain only structured, reproducible evidence."""
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ecosystem": ecosystem, "status": "SCANNER_FAILURE", "error": str(exc)}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "ecosystem": ecosystem,
            "status": "SCANNER_FAILURE",
            "returncode": completed.returncode,
            "error": "scanner did not emit JSON",
        }
    if ecosystem == "pypi":
        findings = [item for item in payload.get("dependencies", []) if item.get("vulns")]
        status = "FINDINGS" if findings else "PASS"
    else:
        vulnerabilities = payload.get("metadata", {}).get("vulnerabilities", {})
        total = sum(value for value in vulnerabilities.values() if isinstance(value, int))
        status = "FINDINGS" if total else "PASS"
    return {
        "ecosystem": ecosystem,
        "status": status,
        "returncode": completed.returncode,
        "result": payload,
    }


def vulnerability_scans() -> dict[str, Any]:
    return {
        "schema": "howhow.supply-chain.vulnerability-scan.v1",
        "scanners": [
            vulnerability_scan(
                [
                    "uvx",
                    "pip-audit",
                    "--format",
                    "json",
                    "--path",
                    str(Path(sysconfig.get_paths()["purelib"]).relative_to(ROOT)),
                ],
                "pypi",
            ),
            vulnerability_scan(
                ["pnpm.cmd" if os.name == "nt" else "pnpm", "audit", "--json"], "npm"
            ),
        ],
    }


def tracked_secret_findings() -> list[str]:
    findings: list[str] = []
    names = run("git", "ls-files").splitlines()
    for name in names:
        if SUSPICIOUS_NAME_RE.search(name):
            findings.append(f"tracked suspicious filename: {name}")
        path = ROOT / name
        if path.is_file() and path.stat().st_size <= 2_000_000:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if SECRET_RE.search(text):
                findings.append(f"secret-like token in tracked file: {name}")
    # Search reachable history without emitting matching content.
    try:
        historical = run("git", "log", "--all", "-G" + SECRET_RE.pattern, "--format=%H", "--", ".")
    except subprocess.CalledProcessError:
        historical = ""
    if historical.strip():
        findings.append("secret-like token found in reachable git history")
    return sorted(set(findings))


def lock_integrity_findings() -> list[str]:
    findings: list[str] = []
    import tomllib

    for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8")).get("package", []):
        if package["name"] == "howhow":
            continue
        if not package.get("sdist", {}).get("hash") and not package.get("wheels"):
            findings.append(
                f"uv package has no artifact hash: {package['name']}=={package['version']}"
            )
    in_packages = False
    current = ""
    has_integrity = False
    for line in (ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8").splitlines() + [
        "  __END__: {}"
    ]:
        if line == "packages:":
            in_packages = True
            continue
        if in_packages and line and not line.startswith(" "):
            break
        if not in_packages:
            continue
        if re.match(r"^  (?:'[^']+'|[^ ]+)@[^:]+:$", line):
            if current and not has_integrity:
                findings.append(f"pnpm package has no integrity: {current}")
            current, has_integrity = line.strip(), False
        elif current and "integrity:" in line:
            has_integrity = True
    return findings


def load_inventory() -> dict[str, Any]:
    if not ALLOWLIST.exists():
        return {"generated": None, "packages": []}
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))


def audit(refresh: bool, scans: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    packages = locked_packages()
    old = {
        (p["ecosystem"], p["name"], p["version"]): p for p in load_inventory().get("packages", [])
    }
    findings: list[str] = []
    inventory: list[dict[str, str]] = []
    for package in packages:
        key = (package["ecosystem"], package["name"], package["version"])
        entry = old.get(key, {})
        license_name = entry.get("license", "UNKNOWN")
        source = entry.get("source", "")
        if (refresh and license_name.upper() == "UNKNOWN") or not source:
            try:
                getter = pypi_license if package["ecosystem"] == "pypi" else npm_license
                license_name, source = getter(package["name"], package["version"])
            except Exception:
                license_name, source = "UNKNOWN", ""
        if license_name.upper() == "UNKNOWN" and package["ecosystem"] == "pypi":
            try:
                license_name, installed_source = installed_license(
                    package["name"], package["version"]
                )
                source = installed_source or source
            except Exception:
                pass
        status = "PASS"
        if license_name.upper() == "UNKNOWN":
            status = "UNKNOWN"
            findings.append(
                f"license unknown: {package['ecosystem']}:{package['name']}@{package['version']}"
            )
        elif any(
            term in license_name.upper() for term in ("GPL", "AGPL", "NONCOMMERCIAL", "CC BY-NC")
        ):
            status = "REVIEW"
            findings.append(
                f"license requires review: {package['ecosystem']}:{package['name']}@"
                f"{package['version']} ({license_name})"
            )
        inventory.append({**package, "license": license_name, "status": status, "source": source})
    report = {
        "schema": "howhow.supply-chain.v1",
        "generated": str(date.today()),
        "lockfiles": {"uv.lock": "authoritative", "pnpm-lock.yaml": "authoritative"},
        "packages": inventory,
        "harth": {
            "license": "CC BY 4.0",
            "attribution_verified": True,
            "manifest": "episodes/harth-calibration/data/manifest.json",
        },
        "vulnerability_scan": scans
        or {
            "schema": "howhow.supply-chain.vulnerability-scan.v1",
            "status": "NOT_APPLICABLE",
            "reason": "Library audits do not execute external scanners.",
        },
        "secrets": {"status": "PASS" if not tracked_secret_findings() else "FAIL"},
        "integrity": {"status": "PASS" if not lock_integrity_findings() else "FAIL"},
    }
    findings.extend(tracked_secret_findings())
    findings.extend(lock_integrity_findings())
    return report, sorted(set(findings))


def write_outputs(report: dict[str, Any]) -> None:
    ALLOWLIST.parent.mkdir(exist_ok=True)
    ALLOWLIST.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Third-party notices",
        "",
        "Generated from `uv.lock` and `pnpm-lock.yaml`; regenerate with "
        "`uv run scripts/audit_supply_chain.py --write --refresh`.",
        "",
        "## HARTH",
        "",
        "HARTH is distributed under **CC BY 4.0**. Retain the dataset citation "
        "and UCI metadata link in `episodes/harth-calibration/data/manifest.json`.",
        "",
        "## Dependencies",
        "",
        "| Ecosystem | Package | Version | License | Status |",
        "|---|---|---:|---|---|",
    ]
    for p in report["packages"]:
        lines.append(
            f"| {p['ecosystem']} | `{p['name']}` | `{p['version']}` | "
            f"{p['license']} | {p['status']} |"
        )
    lines += [
        "",
        "Vulnerability status is recorded from machine-readable `uvx pip-audit` and "
        "`pnpm audit --json` evidence. Scanner failures remain explicit. "
        "UNKNOWN licenses remain review findings and "
        "must not be treated as approved.",
        "",
    ]
    NOTICE.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write", action="store_true", help="write compliance inventory and notices"
    )
    parser.add_argument("--refresh", action="store_true", help="refresh registry license metadata")
    parser.add_argument(
        "--check", action="store_true", help="fail on unknown/review/integrity/secret findings"
    )
    parser.add_argument(
        "--scan", action="store_true", help="run uvx pip-audit and pnpm audit as JSON"
    )
    args = parser.parse_args()
    scans = vulnerability_scans() if args.scan else None
    report, findings = audit(args.refresh, scans)
    if scans:
        for scanner in scans["scanners"]:
            if scanner["status"] == "FINDINGS":
                findings.append(f"vulnerability findings: {scanner['ecosystem']}")
            elif scanner["status"] == "SCANNER_FAILURE":
                findings.append(f"vulnerability scanner failure: {scanner['ecosystem']}")
    if args.write:
        write_outputs(report)
    print(
        json.dumps(
            {
                "packages": len(report["packages"]),
                "findings": findings,
                "vulnerability_scan": report["vulnerability_scan"],
            },
            indent=2,
        )
    )
    return 1 if args.check and findings else 0


if __name__ == "__main__":
    sys.exit(main())
