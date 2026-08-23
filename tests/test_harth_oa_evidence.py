import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "harth_oa", Path("scripts/build_harth_oa_evidence.py")
)
assert _SPEC and _SPEC.loader
MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MODULE)
build = MODULE.build


def test_fixture_manifest_is_bounded_and_does_not_store_full_text(tmp_path: Path):
    report = build(live=False, output=tmp_path)
    assert report["status"] == "UNVERIFIED"
    rows = [json.loads(line) for line in (tmp_path / "oa-evidence.jsonl").read_text().splitlines()]
    assert len(rows) == 6
    assert all(row["record_type"] == "SourceRecord" for row in rows)
    assert all("raw_sha256" in row for row in rows)
    assert "full_text" not in " ".join(rows[0])


def test_live_fixture_transport_records_verified_quote_and_hash(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(MODULE, "SOURCES", (MODULE.SOURCES[0],))
    monkeypatch.setattr(
        MODULE,
        "fetch",
        lambda url: (b"<html>Twenty-two participants were recorded.</html>", "text/html"),
    )
    report = build(live=True, output=tmp_path)
    assert report["failures"] == []
    rows = [json.loads(line) for line in (tmp_path / "oa-evidence.jsonl").read_text().splitlines()]
    assert rows[0]["access"] == "VERIFIED"
    assert rows[1]["status"] == "VERIFIED"
    assert len(rows[0]["raw_sha256"]) == 64
