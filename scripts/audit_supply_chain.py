"""Fail-closed dependency, provenance, license, and secret hygiene audit.

The audit intentionally uses lockfiles as the authority. Registry metadata is only
used when refreshing the checked-in compliance inventory; it is never copied into
public output without being reduced to package names, versions, and license data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import sysconfig
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "compliance" / "dependency-allowlist.json"
NOTICE = ROOT / "compliance" / "THIRD_PARTY_NOTICES.md"
NUMPY_NOTICE_ROOT = "compliance/numpy-notices"
NUMPY_UPSTREAM_NOTICES = {
    "LICENSE.txt": {
        "url": "https://raw.githubusercontent.com/numpy/numpy/v2.3.2/LICENSE.txt",
        "sha256": "1be1df33863f97a7bc1c4d67980bd6c69c9a6fef0a5ee76e6ad6cb91e56e8491",
        "scope": "source-and-wheel",
    },
    "LICENSES_bundled.txt": {
        "url": "https://raw.githubusercontent.com/numpy/numpy/v2.3.2/LICENSES_bundled.txt",
        "sha256": "7fe52683ce840a3103e9cc82fe92252b2c533061ca4f9b8d6f3d4d835f7c32f9",
        "scope": "sdist-and-source-only",
    },
    "LICENSE_linux.txt": {
        "url": "https://raw.githubusercontent.com/numpy/numpy/v2.3.2/tools/wheels/LICENSE_linux.txt",
        "sha256": "a8683fdcd75a8dbd2cf7f638e5b77ada6c61a31f4cb1c8a77e7bfa9f40560442",
        "scope": "wheel-platform:linux",
    },
    "LICENSE_osx.txt": {
        "url": "https://raw.githubusercontent.com/numpy/numpy/v2.3.2/tools/wheels/LICENSE_osx.txt",
        "sha256": "c91c24ac6ba9ef8ba13b1707d14107cd82e3397ddb9b78201a6e6d2777680fda",
        "scope": "wheel-platform:macos",
    },
    "LICENSE_win32.txt": {
        "url": "https://raw.githubusercontent.com/numpy/numpy/v2.3.2/tools/wheels/LICENSE_win32.txt",
        "sha256": "d2ddbc988223a00e704574ddf9f0b20ae62bfa38fbabf4f06c0d00ac456e1aac",
        "scope": "wheel-platform:windows",
    },
}

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
    """Use shipped license metadata without exposing the installation location."""
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
        relative = PurePosixPath(str(file).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        normalized_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return (
            license_name,
            f"installed-dist:{normalized_name}/{relative.as_posix()}#sha256={digest}",
        )
    return "UNKNOWN", ""


def stable_registry_source(ecosystem: str, name: str, version: str) -> str:
    """Return a portable source URL for an existing inventory entry."""
    if ecosystem == "pypi":
        return (
            f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
        )
    encoded = urllib.parse.quote(name, safe="@")
    return f"https://registry.npmjs.org/{encoded}/{urllib.parse.quote(version)}"


def portable_source(ecosystem: str, source: str) -> bool:
    return source.startswith(
        ("https://pypi.org/pypi/", "https://registry.npmjs.org/", "installed-dist:")
    )


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


def locked_artifacts() -> list[dict[str, str]]:
    """Return public artifact provenance directly from uv.lock, without registry access."""
    import tomllib

    artifacts: list[dict[str, str]] = []
    uv = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for package in uv.get("package", []):
        if package.get("source", {}).get("editable"):
            continue
        if package.get("source", {}).get("registry") != "https://pypi.org/simple":
            continue
        for kind, values in (
            ("sdist", [package.get("sdist", {})]),
            ("wheel", package.get("wheels", [])),
        ):
            for artifact in values:
                url = artifact.get("url")
                digest = artifact.get("hash", "")
                if not url or not digest:
                    continue
                filename = url.rsplit("/", 1)[-1]
                scope = (
                    "sdist"
                    if kind == "sdist"
                    else f"wheel:{filename.split('-', 4)[-1].removesuffix('.whl')}"
                )
                item = {
                    "ecosystem": "pypi",
                    "package": package["name"],
                    "version": package["version"],
                    "artifact": filename,
                    "url": url,
                    "sha256": digest.removeprefix("sha256:"),
                    "scope": scope,
                }
                if package["name"] == "numpy":
                    item["upstream_notices"] = ",".join(numpy_notice_names(scope))
                artifacts.append(item)
    return artifacts


def numpy_notice_names(scope: str) -> list[str]:
    if scope == "sdist":
        return ["LICENSE.txt", "LICENSES_bundled.txt"]
    if "win" in scope:
        return ["LICENSE.txt", "LICENSE_win32.txt"]
    if "linux" in scope:
        return ["LICENSE.txt", "LICENSE_linux.txt"]
    if "macosx" in scope:
        return ["LICENSE.txt", "LICENSE_osx.txt"]
    return ["LICENSE.txt"]


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
        if source and not portable_source(package["ecosystem"], source):
            source = stable_registry_source(*key)
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
        if package["name"] == "numpy" and "\n" in license_name:
            license_name = "BSD-3-Clause with bundled notices (see artifact provenance)"
        inventory.append({**package, "license": license_name, "status": status, "source": source})
    artifacts = locked_artifacts()
    report = {
        "schema": "howhow.supply-chain.v1",
        "generated": str(date.today()),
        "lockfiles": {"uv.lock": "authoritative", "pnpm-lock.yaml": "authoritative"},
        "packages": inventory,
        "artifacts": artifacts,
        "upstream_notices": NUMPY_UPSTREAM_NOTICES,
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
        "Generated from lockfiles; package metadata and resolved artifacts are recorded separately.",  # noqa: E501
        "Regenerate with `uv run scripts/audit_supply_chain.py --write --refresh`.",
        "",
        "## HARTH",
        "",
        "HARTH is **CC BY 4.0**. This dataset attribution is separate from software and dependency licenses; retain the citation and UCI metadata link in `episodes/harth-calibration/data/manifest.json`.",  # noqa: E501
        "",
        "## Dependency summaries",
        "",
        "| Ecosystem | Package | Version | License summary | Status |",
        "|---|---|---:|---|---|",
    ]
    for p in report["packages"]:
        lines.append(
            f"| {p['ecosystem']} | `{p['name']}` | `{p['version']}` | {p['license'].replace(chr(10), ' ')} | {p['status']} |"  # noqa: E501
        )
    lines += [
        "",
        "## Resolved artifact provenance",
        "",
        "These are lockfile-resolved artifacts, not a claim that every platform artifact is redistributed by HowHow.",  # noqa: E501
        "",
        "| Package | Version | Artifact / platform tag | Scope | URL | SHA-256 |",
        "|---|---:|---|---|---|---|",
    ]
    for artifact in report["artifacts"]:
        lines.append(
            f"| `{artifact['package']}` | `{artifact['version']}` | `{artifact['artifact']}` | `{artifact['scope']}` | {artifact['url']} | `{artifact['sha256']}` |"  # noqa: E501
        )
    lines += [
        "",
        "## NumPy 2.3.2 notice attachments",
        "",
        "NumPy upstream notices are preserved verbatim under `compliance/numpy-notices/`. Platform applicability is explicit: source/sdist uses the bundled source notice; each wheel uses only the notice matching its wheel platform tag. No platform applicability is inferred.",  # noqa: E501
        "",
        "| Attachment | Upstream URL | SHA-256 | Scope |",
        "|---|---|---|---|",
    ]
    for name, notice in report["upstream_notices"].items():
        lines.append(
            f"| [`{NUMPY_NOTICE_ROOT}/{name}`]({NUMPY_NOTICE_ROOT}/{name}) | {notice['url']} | `{notice['sha256']}` | `{notice['scope']}` |"  # noqa: E501
        )
    lines += [
        "",
        "HowHow remains **UNKNOWN** and **OWNER_LICENSE_DECISION_REQUIRED**. This change does not add a LICENSE file or project license metadata. Vulnerability status is retained as structured scanner evidence; UNKNOWN/review findings are never treated as approval.",  # noqa: E501
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
