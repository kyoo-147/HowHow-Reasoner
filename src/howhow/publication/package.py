"""Package manifests and clean source archives for human review."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .latex import BuildConfig, BuildResult, LatexBuilder, LatexBuildError


class PackageValidationError(RuntimeError):
    pass


@dataclass
class PackageResult:
    output: Path
    build: BuildResult | None
    ready: bool
    gates: dict[str, bool]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class PackageBuilder:
    def __init__(self, latex: BuildConfig | None = None) -> None:
        self.latex = LatexBuilder(latex)

    def build(
        self,
        source: Path,
        output: Path,
        *,
        evidence_reviewed: bool = False,
        human_reviewed: bool = False,
        reproducible: bool = False,
        claim_map: Path | None = None,
    ) -> PackageResult:
        source = source.resolve()
        output = output.resolve()
        if output == source or output.is_relative_to(source):
            raise PackageValidationError("output must be outside source")
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True)
        staging = output / ".staging"
        try:
            build = self.latex.build(source, staging)
        except LatexBuildError:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        for name in ("main.tex", "references.bib"):
            candidate = staging / name
            if candidate.exists():
                shutil.copy2(candidate, output / name)
        if (staging / "figures").is_dir():
            shutil.copytree(staging / "figures", output / "figures")
        shutil.copy2(build.pdf, output / "paper.pdf")
        (output / "build.log").write_text(build.log, encoding="utf-8")
        shutil.copytree(staging, output / "source", dirs_exist_ok=True)
        shutil.rmtree(staging)
        if claim_map and claim_map.is_file():
            shutil.copy2(claim_map, output / "CLAIM_EVIDENCE_MAP.md")
        elif not (output / "CLAIM_EVIDENCE_MAP.md").exists():
            (output / "CLAIM_EVIDENCE_MAP.md").write_text(
                "# Claim-evidence map\n\nSynthetic fixture: no research evidence is asserted.\n",
                encoding="utf-8",
            )
        (output / "LICENSE_ACCESS_MANIFEST.md").write_text(
            "# License and access manifest\n\n"
            "All fixture material is synthetic and included for build mechanics only.\n",
            encoding="utf-8",
        )
        (output / "REPRODUCIBILITY.md").write_text(
            "# Reproducibility\n\n"
            "Build from `source/` with the configured TeX executable and bounded timeout.\n",
            encoding="utf-8",
        )
        files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "checksums.txt")
        checksums = (
            "\n".join(f"{_sha(p)}  {p.relative_to(output).as_posix()}" for p in files) + "\n"
        )
        (output / "checksums.txt").write_text(checksums, encoding="utf-8")
        with zipfile.ZipFile(
            output / "arxiv-source.zip", "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted((output / "source").rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(output / "source").as_posix())
        gates = {
            "package": True,
            "evidence": evidence_reviewed,
            "human_review": human_reviewed,
            "reproducibility": reproducible,
        }
        ready = all(gates.values())
        manifest = {
            "schema_version": "v1",
            "package_id": f"package:{_sha(output / 'paper.pdf')[:16]}",
            "created_at": datetime.now(UTC).isoformat(),
            "label": "READY FOR HUMAN REVIEW" if ready else "PACKAGING",
            "completed": ready,
            "gates": gates,
            "artifacts": [p.relative_to(output).as_posix() for p in files] + ["arxiv-source.zip"],
            "scientific_evidence": "UNVERIFIED: synthetic fixture; package mechanics only",
        }
        (output / "package-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return PackageResult(output, build, ready, gates)

    @staticmethod
    def check(output: Path) -> dict[str, bool]:
        output = output.resolve()
        required = (
            "paper.pdf",
            "arxiv-source.zip",
            "checksums.txt",
            "package-manifest.json",
            "CLAIM_EVIDENCE_MAP.md",
            "LICENSE_ACCESS_MANIFEST.md",
        )
        missing = [name for name in required if not (output / name).is_file()]
        if missing:
            raise PackageValidationError(f"missing package artifacts: {', '.join(missing)}")
        manifest = json.loads((output / "package-manifest.json").read_text(encoding="utf-8"))
        if manifest.get("label") == "READY FOR HUMAN REVIEW" and not all(
            manifest.get("gates", {}).values()
        ):
            raise PackageValidationError("READY FOR HUMAN REVIEW requires every configured gate")
        expected = {}
        for line in (output / "checksums.txt").read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            expected[name] = digest
        for name, digest in expected.items():
            if not (output / name).is_file() or _sha(output / name) != digest:
                raise PackageValidationError(f"checksum mismatch: {name}")
        return {
            "artifacts": True,
            "checksums": True,
            "gates": all(manifest.get("gates", {}).values()),
        }
