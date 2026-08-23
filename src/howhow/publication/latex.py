"""Safe LaTeX compilation in an isolated staging directory.

This adapter checks source references before invoking TeX. It never shells through a
command string and never treats a successful TeX process as scientific validation.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class LatexBuildError(RuntimeError):
    """A source, tool, or bounded compilation failure."""


@dataclass(frozen=True)
class BuildConfig:
    latex: str | Path | None = None
    bibtex: str | Path | None = None
    biber: str | Path | None = None
    timeout_seconds: int = 120
    passes: int = 2


@dataclass
class BuildResult:
    staging: Path
    pdf: Path
    log: str
    bibliography_tool: str | None
    commands: list[list[str]] = field(default_factory=list)


_COMMAND = re.compile(
    r"\\(?P<name>cite|citep|citet|ref|pageref|input|include|includegraphics)\s*(?:\[[^]]*\])?\s*\{([^}]*)\}"
)
_LABEL = re.compile(r"\\label\s*\{([^}]+)\}")
_BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,]+)", re.I)
_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|//|\\\\|/)")


def _tool(value: str | Path | None, names: tuple[str, ...], role: str) -> str | None:
    if value is not None:
        path = Path(value)
        if not path.is_file():
            raise LatexBuildError(f"{role} executable is not a file: {path}")
        return str(path.resolve())
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


class LatexBuilder:
    def __init__(self, config: BuildConfig | None = None) -> None:
        self.config = config or BuildConfig()
        if self.config.timeout_seconds < 1 or self.config.passes < 1:
            raise ValueError("timeout_seconds and passes must be positive")

    def build(self, source: Path, staging: Path) -> BuildResult:
        source = source.resolve()
        if not source.is_dir():
            raise LatexBuildError(f"source directory does not exist: {source}")
        main = source / "main.tex"
        if not main.is_file():
            raise LatexBuildError("source must contain main.tex")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging)
        tex = "\n".join(p.read_text(encoding="utf-8") for p in staging.rglob("*.tex"))
        self._validate(tex, staging)
        latex = _tool(self.config.latex, ("latexmk", "pdflatex"), "LaTeX")
        if latex is None:
            raise LatexBuildError("LaTeX executable not found; configure --latex or install TeX")
        bib = list(staging.rglob("*.bib"))
        bibliography_tool: str | None = None
        if bib:
            bibliography_tool = self._bibliography_tool(staging)
        commands: list[list[str]] = []
        logs: list[str] = []
        if Path(latex).name.lower().startswith("latexmk"):
            cmd = [latex, "-interaction=nonstopmode", "-halt-on-error", "-pdf", "main.tex"]
            commands.append(cmd)
            logs.append(self._run(cmd, staging))
        else:
            for _ in range(self.config.passes):
                cmd = [latex, "-interaction=nonstopmode", "-halt-on-error", "main.tex"]
                commands.append(cmd)
                logs.append(self._run(cmd, staging))
                if bibliography_tool:
                    tool = _tool(
                        getattr(self.config, Path(bibliography_tool).name, None),
                        (bibliography_tool,),
                        "bibliography",
                    )
                    if tool is None:
                        raise LatexBuildError(f"{bibliography_tool} executable not found")
                    bcmd = [tool, "main"] if bibliography_tool == "bibtex" else [tool, "main"]
                    commands.append(bcmd)
                    logs.append(self._run(bcmd, staging))
        pdf = staging / "main.pdf"
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise LatexBuildError("LaTeX completed without a non-empty main.pdf")
        return BuildResult(staging, pdf, "\n".join(logs), bibliography_tool, commands)

    def _bibliography_tool(self, staging: Path) -> str:
        text = "\n".join(
            p.read_text(encoding="utf-8", errors="replace") for p in staging.rglob("*.tex")
        )
        if "biblatex" in text or "\\addbibresource" in text:
            tool = _tool(self.config.biber, ("biber",), "Biber")
            if tool is None:
                raise LatexBuildError("biblatex source requires biber")
            return "biber"
        tool = _tool(self.config.bibtex, ("bibtex",), "BibTeX")
        if tool is None:
            raise LatexBuildError("bibliography source requires bibtex")
        return "bibtex"

    def _validate(self, tex: str, root: Path) -> None:
        labels = set(_LABEL.findall(tex))
        bib_keys = {
            m.group(1).strip()
            for p in root.rglob("*.bib")
            for m in _BIB_ENTRY.finditer(p.read_text(encoding="utf-8", errors="replace"))
        }
        for match in _COMMAND.finditer(tex):
            name, raw = match.group(1), match.group(2)
            values = [v.strip() for v in raw.split(",")]
            if any(_ABSOLUTE.search(v) for v in values):
                raise LatexBuildError(f"absolute path rejected in \\{name}: {raw}")
            if name in {"ref", "pageref"}:
                missing = [v for v in values if v not in labels]
                if missing:
                    raise LatexBuildError(f"missing reference(s): {', '.join(missing)}")
            elif name.startswith("cite"):
                missing = [v for v in values if v not in bib_keys]
                if missing:
                    raise LatexBuildError(f"missing citation(s): {', '.join(missing)}")
            elif name == "includegraphics":
                candidate = next((root / v for v in values), None)
                candidates = [candidate] if candidate else []
                if candidate and not candidate.suffix:
                    candidates.extend(
                        root / (str(candidate) + ext) for ext in (".pdf", ".png", ".jpg", ".jpeg")
                    )
                if not any(p.is_file() for p in candidates):
                    raise LatexBuildError(f"missing figure: {raw}")

    def _run(self, command: list[str], cwd: Path) -> str:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LatexBuildError(f"bounded subprocess timeout: {' '.join(command)}") from exc
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode:
            raise LatexBuildError(
                f"LaTeX command failed ({completed.returncode}): {output[-4000:]}"
            )
        return output
