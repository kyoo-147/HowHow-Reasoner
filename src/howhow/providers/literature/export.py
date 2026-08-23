"""HARTH literature acquisition/export vertical slice."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from howhow.evidence.core import EvidenceSpan
from howhow.evidence.retrieval import CacheHook, FileCache, RetrievalError

from .adapters import PROVIDERS, Paper, ProviderConfig


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json(v) for v in value]
    return value


def _records(paper: Paper) -> list[dict[str, Any]]:
    source = _json(asdict(paper.source))
    rows = [{"record_type": "SourceRecord", **source}]
    if paper.title:
        rows.append(
            {
                "record_type": "EvidenceSpan",
                **_json(
                    asdict(
                        EvidenceSpan(
                            "title:" + paper.source.source_id,
                            paper.source.source_id,
                            "metadata:title",
                            paper.title,
                        )
                    )
                ),
            }
        )
    if paper.abstract.strip():
        rows.append(
            {
                "record_type": "EvidenceSpan",
                **_json(
                    asdict(
                        EvidenceSpan(
                            "abstract:" + paper.source.source_id,
                            paper.source.source_id,
                            "metadata:abstract",
                            paper.abstract.strip(),
                        )
                    )
                ),
            }
        )
    return rows


def export_harth(
    output: Path,
    *,
    providers: tuple[str, ...] = ("crossref", "openalex"),
    limit: int = 3,
    cache_dir: Path | None = None,
    live: bool = False,
    config: ProviderConfig | None = None,
) -> dict[str, Any]:
    """Write portable JSONL. Source text is retained only as bounded metadata snippets."""
    if limit < 1 or limit > 10:
        raise ValueError("limit must be between 1 and 10")
    cache: CacheHook | None = FileCache(cache_dir) if cache_dir else None
    rows: list[dict[str, Any]] = []
    failures = []
    seen = set()
    queries = (
        "HARTH dataset human activity recognition",
        "human activity recognition wearable sensors subject held out",
    )
    for name in providers:
        if name not in PROVIDERS:
            raise ValueError(f"unknown provider: {name}")
        adapter = PROVIDERS[name](cache=cache, config=config)
        for query in queries[: 1 if not live else 2]:
            try:
                papers = adapter.search(query, limit=limit)
            except RetrievalError as error:
                failures.append(
                    {
                        "provider": name,
                        "query": query,
                        "status": "UNVERIFIED",
                        "failure": error.classification.value,
                        "message": str(error),
                        "attempts": error.attempts,
                    }
                )
                continue
            for paper in papers:
                key = (paper.source.provider, paper.source.stable_id, paper.source.version)
                if key not in seen:
                    seen.add(key)
                    rows.extend(_records(paper))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        for failure in failures:
            stream.write(
                json.dumps({"record_type": "ProviderFailure", **failure}, sort_keys=True) + "\n"
            )
    return {
        "output": str(output),
        "records": len(rows),
        "providers": list(providers),
        "failures": failures,
        "status": "VERIFIED" if rows and not failures else "UNVERIFIED",
    }


def live_smoke(*, cache_dir: Path, limit: int = 1) -> dict[str, Any]:
    """Opt-in network smoke; result is metadata only and must not be called fixture proof."""
    return export_harth(
        cache_dir.parent / "live-smoke.jsonl",
        providers=("crossref", "openalex"),
        limit=limit,
        cache_dir=cache_dir,
        live=True,
    )
