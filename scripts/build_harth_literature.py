"""Fetch a bounded, metadata-only HARTH related-work corpus."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DOIS = {
    "10.3390/s21237853": "HARTH dataset and leave-one-subject-out baseline",
    "10.3390/s21196566": "HAR calibration and temperature scaling",
    "10.1007/s00521-024-09505-4": "NLL and Brier proper scoring rules under distribution shift",
}

ARXIV = {
    "1706.04599": "temperature scaling for modern neural networks",
    "1506.02142": "probabilistic calibration and ECE",
    "2403.15422": "HAR data heterogeneity and distribution shift",
}


def fetch(doi: str) -> tuple[dict, bytes]:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "howhow-harth-corpus/1.0 research@example.org",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()
    return json.loads(body)["message"], body


def main() -> None:
    out = Path("episodes/harth-calibration/literature")
    out.mkdir(parents=True, exist_ok=True)
    retrieved = datetime.now(UTC).isoformat()
    rows: list[dict] = []
    failures: list[dict] = []
    for doi, role in DOIS.items():
        try:
            item, raw = fetch(doi)
        except Exception as exc:  # bounded live retrieval is allowed to be partial
            failures.append({"doi": doi, "status": "UNVERIFIED", "error": type(exc).__name__})
            continue
        title = (item.get("title") or [""])[0]
        abstract = item.get("abstract") or ""
        quote = " ".join(abstract.replace("<jats:p>", "").replace("</jats:p>", "").split())
        quote = quote[:500].rsplit(" ", 1)[0] + ("…" if len(quote) > 500 else "") if quote else ""
        source_id = f"crossref:{doi}"
        rows.append(
            {
                "record_type": "SourceRecord",
                "source_id": source_id,
                "provider": "crossref",
                "stable_id": doi,
                "version": None,
                "access": "unknown",
                "retrieved_at": retrieved,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "title": title,
                "url": f"https://doi.org/{doi}",
                "abstract": quote,
                "doi": doi,
                "role": role,
                "license": None,
            }
        )
        rows.append(
            {
                "record_type": "EvidenceSpan",
                "evidence_id": f"title:{source_id}",
                "source_id": source_id,
                "kind": "text",
                "locator": "metadata:title",
                "quote": title,
                "status": "UNVERIFIED",
            }
        )
        if abstract:
            rows.append(
                {
                    "record_type": "EvidenceSpan",
                    "evidence_id": f"abstract:{source_id}",
                    "source_id": source_id,
                    "kind": "text",
                    "locator": "metadata:abstract:first-500-chars",
                    "quote": quote,
                    "status": "UNVERIFIED",
                }
            )
    (out / "sources.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (out / "retrieval-report.json").write_text(
        json.dumps(
            {
                "retrieved_at": retrieved,
                "sources": sum(row["record_type"] == "SourceRecord" for row in rows),
                "failures": failures,
                "status": "VERIFIED" if not failures else "UNVERIFIED",
                "policy": "metadata-only; no copyrighted full text",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    import urllib.parse

    main()
