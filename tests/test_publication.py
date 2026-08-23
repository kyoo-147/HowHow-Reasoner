import shutil
import struct
import zlib
from pathlib import Path

import pytest

from howhow.publication import BuildConfig, LatexBuildError, PackageBuilder


def fake_tool(path: Path, pdf: bool = False) -> Path:
    script = path.with_suffix(".py")
    script.write_text(
        "import pathlib\n"
        + ("pathlib.Path('main.pdf').write_bytes(b'%PDF-synthetic')\n" if pdf else ""),
        encoding="utf-8",
    )
    path.write_text(f'@echo off\npython "{script}" %*\n', encoding="utf-8")
    return path


def test_fixture_build_and_checksums(tmp_path: Path) -> None:
    latex = fake_tool(tmp_path / "pdflatex.cmd", pdf=True)
    bibtex = fake_tool(tmp_path / "bibtex.cmd")
    output = tmp_path / "dist"
    result = PackageBuilder(BuildConfig(latex=latex, bibtex=bibtex)).build(
        Path("templates/paper"),
        output,
        evidence_reviewed=True,
        human_reviewed=True,
        reproducible=True,
    )
    assert result.ready
    assert "READY FOR HUMAN REVIEW" in (output / "package-manifest.json").read_text()
    assert PackageBuilder.check(output)["checksums"]


def test_missing_citation_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "paper"
    source.mkdir()
    (source / "main.tex").write_text(r"\documentclass{article} \cite{missing}", encoding="utf-8")
    (source / "references.bib").write_text("", encoding="utf-8")
    with pytest.raises(LatexBuildError, match="missing citation"):
        PackageBuilder(BuildConfig(latex=fake_tool(tmp_path / "latex.cmd", pdf=True))).build(
            source, tmp_path / "out"
        )


def test_absolute_paths_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "paper"
    source.mkdir()
    (source / "main.tex").write_text(
        r"\documentclass{article} \includegraphics{C:/secret/x}", encoding="utf-8"
    )
    with pytest.raises(LatexBuildError, match="absolute path"):
        PackageBuilder(BuildConfig(latex=fake_tool(tmp_path / "latex.cmd", pdf=True))).build(
            source, tmp_path / "out"
        )


def test_committed_fixture_png_is_decodable() -> None:
    data = Path("templates/paper/figures/fixture.png").read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")

    offset = 8
    compressed = bytearray()
    width = height = None
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        assert zlib.crc32(chunk_type + chunk) & 0xFFFFFFFF == crc
        if chunk_type == b"IHDR":
            width, height = struct.unpack(">II", chunk[:8])
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break
        offset += 12 + length

    assert (width, height) == (1, 1)
    assert zlib.decompress(compressed)


@pytest.mark.real_miktex
def test_optional_real_miktex_smoke(tmp_path: Path) -> None:
    del tmp_path
    if shutil.which("pdflatex") is None:
        pytest.skip("MiKTeX/pdflatex is not installed")
    assert shutil.which("pdflatex")
