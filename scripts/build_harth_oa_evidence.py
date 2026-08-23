"""Build a bounded HARTH open-access evidence corpus without storing full text.

The live mode fetches only HTML/JSON landing pages from explicitly listed providers. It
stores provenance, a hash of the response, and short quotation spans; response bodies
(including PDFs) are never written to the repository. Fixture mode is deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

OUT = Path("episodes/harth-calibration/literature")
SOURCES = (
    {
        "id": "oa:harth",
        "doi": "10.3390/s21237853",
        "title": "HARTH: A Human Activity Recognition Dataset for Machine Learning",
        "url": "https://www.mdpi.com/1424-8220/21/23/7853",
        "license": "CC BY 4.0",
        "topics": ["HARTH", "sensor ablation", "subject-held-out evaluation"],
    },
    {
        "id": "oa:har-calibration",
        "doi": "10.3390/s21196566",
        "title": "Confidence-Calibrated Human Activity Recognition",
        "url": "https://www.mdpi.com/1424-8220/21/19/6566",
        "license": "CC BY 4.0",
        "topics": ["HAR calibration", "temperature scaling"],
    },
    {
        "id": "oa:temperature-scaling",
        "arxiv": "1706.04599",
        "title": "On Calibration of Modern Neural Networks",
        "url": "https://arxiv.org/html/1706.04599",
        "license": "arXiv author manuscript; reuse subject to source terms",
        "topics": ["temperature scaling"],
    },
    {
        "id": "oa:ece-limitations",
        "arxiv": "1904.01685",
        "title": "Measuring Calibration in Deep Learning",
        "url": "https://arxiv.org/html/1904.01685",
        "license": "arXiv author manuscript; reuse subject to source terms",
        "topics": ["ECE limitations"],
    },
    {
        "id": "oa:proper-scoring",
        "doi": "10.1214/20-STS812",
        "title": "Evaluating probabilistic forecasts with scoring rules",
        "url": "https://projecteuclid.org/journals/statistical-science/volume-36/issue-1/Evaluating-Probabilistic-Forecasts-with-Scoring-Rules/10.1214/20-STS812.full",
        "license": "publisher open-access status to be verified",
        "topics": ["proper scoring rules"],
    },
    {
        "id": "oa:subject-shift",
        "arxiv": "2403.15422",
        "title": "Human Activity Recognition under Distribution Shift",
        "url": "https://arxiv.org/html/2403.15422",
        "license": "arXiv author manuscript; reuse subject to source terms",
        "topics": ["subject-held-out evaluation", "distribution shift"],
    },
)


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def value(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def fetch(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "howhow-harth-oa-evidence/1.0 research@example.org",
            "Accept": "text/html,application/xhtml+xml,application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read(), response.headers.get_content_type()


def quote_for(source: dict, text: str) -> tuple[str, str] | None:
    candidates = {
        "oa:harth": ("Twenty-two participants", "section 3.1"),
        "oa:har-calibration": ("confidence calibration", "abstract"),
        "oa:temperature-scaling": ("temperature scaling", "abstract"),
        "oa:ece-limitations": ("Expected Calibration Error", "abstract"),
        "oa:proper-scoring": ("proper scoring rule", "abstract"),
        "oa:subject-shift": ("distribution shift", "abstract"),
    }
    phrase, locator = candidates[source["id"]]
    match = re.search(re.escape(phrase), text, re.IGNORECASE)
    if not match:
        return None
    start = max(0, match.start() - 120)
    end = min(len(text), match.end() + 260)
    return text[start:end].strip(), locator


def build(*, live: bool, output: Path = OUT) -> dict:
    now = datetime.now(UTC).isoformat()
    rows: list[dict] = []
    failures: list[dict] = []
    for source in SOURCES:
        record = {
            "record_type": "SourceRecord",
            "source_id": source["id"],
            "provider": "open-access-landing-page",
            "stable_id": source.get("doi", source.get("arxiv")),
            "doi": source.get("doi"),
            "version": None,
            "title": source["title"],
            "url": source["url"],
            "license": source["license"],
            "topics": source["topics"],
            "retrieved_at": now,
        }
        if not live:
            record.update({"access": "UNVERIFIED", "raw_sha256": None})
            rows.append(record)
            continue
        try:
            raw, content_type = fetch(source["url"])
            if "pdf" in content_type or source["url"].lower().endswith(".pdf"):
                raise ValueError("PDF response refused; HTML evidence is required")
            parser = Text()
            parser.feed(raw.decode("utf-8", errors="replace"))
            text = parser.value()
            record.update(
                {
                    "access": "VERIFIED",
                    "content_type": content_type,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
            rows.append(record)
            found = quote_for(source, text)
            if found:
                quote, locator = found
                rows.append(
                    {
                        "record_type": "EvidenceSpan",
                        "evidence_id": "quote:" + source["id"],
                        "source_id": source["id"],
                        "kind": "text",
                        "locator": locator,
                        "quote": quote,
                        "status": "VERIFIED",
                        "license": source["license"],
                    }
                )
            else:
                failures.append(
                    {
                        "source_id": source["id"],
                        "status": "UNVERIFIED",
                        "failure": "quote_not_found",
                    }
                )
        except (OSError, urllib.error.URLError, ValueError, UnicodeError) as exc:
            record.update({"access": "UNVERIFIED", "raw_sha256": None})
            rows.append(record)
            failures.append(
                {
                    "source_id": source["id"],
                    "status": "UNVERIFIED",
                    "failure": type(exc).__name__,
                    "message": str(exc),
                }
            )
    output.mkdir(parents=True, exist_ok=True)
    (output / "oa-evidence.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = {
        "retrieved_at": now,
        "sources": len(SOURCES),
        "evidence_spans": sum(r["record_type"] == "EvidenceSpan" for r in rows),
        "failures": failures,
        "status": "VERIFIED" if live and not failures else "UNVERIFIED",
        "policy": "HTML/JSON landing pages only; no PDFs or full-text response bodies stored",
    }
    (output / "oa-retrieval-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(build(live=args.live, output=args.output), indent=2))


if __name__ == "__main__":
    main()
