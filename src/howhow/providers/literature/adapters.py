"""Normalized read-only adapters for common scholarly metadata APIs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import quote_plus

from howhow.evidence.core import AccessStatus, SourceRecord, retrieved_now
from howhow.evidence.retrieval import (
    CacheHook,
    HttpResponse,
    Transport,
    default_transport,
    retrieve_json,
)


@dataclass(frozen=True, slots=True)
class Paper:
    source: SourceRecord
    title: str
    abstract: str
    authors: tuple[str, ...]
    doi: str | None = None
    publication_date: str | None = None
    version: str | None = None


class LiteratureAdapter:
    provider: str = ""
    endpoint: str = ""

    def __init__(
        self, *, transport: Transport = default_transport, cache: CacheHook | None = None
    ) -> None:
        self.transport = transport
        self.cache = cache

    def search(self, query: str, *, limit: int = 10) -> tuple[Paper, ...]:
        if not query.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid query or limit")
        payload, response = retrieve_json(
            self.endpoint.format(query=quote_plus(query), limit=limit),
            transport=self.transport,
            cache=self.cache,
        )
        return tuple(self._parse(item, response) for item in self._items(payload)[:limit])

    def _items(self, payload: object) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def _parse(self, item: Mapping[str, Any], response: HttpResponse) -> Paper:
        raise NotImplementedError

    def _paper(
        self,
        *,
        stable_id: str,
        item: Mapping[str, Any],
        response: HttpResponse,
        title: str,
        abstract: str,
        authors: tuple[str, ...],
        doi: str | None = None,
        version: str | None = None,
        date: str | None = None,
        url: str | None = None,
        access: AccessStatus = AccessStatus.UNKNOWN,
    ) -> Paper:
        raw = response.body
        source = SourceRecord(
            source_id=f"{self.provider}:{stable_id}",
            provider=self.provider,
            stable_id=stable_id,
            version=version,
            access=access,
            retrieved_at=retrieved_now(),
            raw_sha256=sha256(raw).hexdigest(),
            title=title,
            url=url,
            abstract=abstract,
        )
        return Paper(source, title, abstract, authors, doi, date, version)


def _authors(value: object, *keys: str) -> tuple[str, ...]:
    result: list[str] = []
    for author in value if isinstance(value, list) else []:
        if isinstance(author, str):
            result.append(author)
        elif isinstance(author, Mapping):
            for key in keys:
                name = author.get(key)
                if isinstance(name, str) and name:
                    result.append(name)
                    break
    return tuple(result)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


class ArxivAdapter(LiteratureAdapter):
    provider, endpoint = (
        "arxiv",
        "https://export.arxiv.org/api/query?search_query=all:{query}&max_results={limit}",
    )

    def _items(self, payload: object) -> list[Mapping[str, Any]]:
        return [x for x in payload.get("entries", [])] if isinstance(payload, Mapping) else []

    def _parse(self, item: Mapping[str, Any], response: HttpResponse) -> Paper:
        stable = _text(item.get("id")).rstrip("/").rsplit("/", 1)[-1]
        return self._paper(
            stable_id=stable,
            item=item,
            response=response,
            title=_text(item.get("title")).strip(),
            abstract=_text(item.get("summary")).strip(),
            authors=_authors(item.get("authors"), "name"),
            version=_text(item.get("version")) or None,
            date=_text(item.get("published")) or None,
            url=_text(item.get("id")),
            access=AccessStatus.OPEN,
        )


class OpenAlexAdapter(LiteratureAdapter):
    provider, endpoint = (
        "openalex",
        "https://api.openalex.org/works?search={query}&per-page={limit}",
    )

    def _items(self, payload: object) -> list[Mapping[str, Any]]:
        return [x for x in payload.get("results", [])] if isinstance(payload, Mapping) else []

    def _parse(self, item: Mapping[str, Any], response: HttpResponse) -> Paper:
        stable = _text(item.get("id")).rstrip("/").rsplit("/", 1)[-1]
        doi = _text(item.get("doi")) or None
        authors = _authors(item.get("authorships"), "display_name")
        return self._paper(
            stable_id=stable,
            item=item,
            response=response,
            title=_text(item.get("title")),
            abstract="",
            authors=authors,
            doi=doi,
            date=_text(item.get("publication_date")) or None,
            url=_text(item.get("id")),
            access=AccessStatus.OPEN
            if item.get("open_access", {}).get("is_oa")
            else AccessStatus.UNKNOWN,
        )


class CrossrefAdapter(LiteratureAdapter):
    provider, endpoint = "crossref", "https://api.crossref.org/works?query={query}&rows={limit}"

    def _items(self, payload: object) -> list[Mapping[str, Any]]:
        return (
            [x for x in payload.get("message", {}).get("items", [])]
            if isinstance(payload, Mapping)
            else []
        )

    def _parse(self, item: Mapping[str, Any], response: HttpResponse) -> Paper:
        doi = _text(item.get("DOI"))
        stable = doi or _text(item.get("URL"))
        title = (
            (item.get("title") or [""])[0]
            if isinstance(item.get("title"), list)
            else _text(item.get("title"))
        )
        return self._paper(
            stable_id=stable,
            item=item,
            response=response,
            title=title,
            abstract=_text(item.get("abstract")),
            authors=_authors(item.get("author"), "given", "family"),
            doi=doi or None,
            date=_text(item.get("published", {}).get("date-time")) or None,
            url=_text(item.get("URL")),
        )


class SemanticScholarAdapter(LiteratureAdapter):
    provider, endpoint = (
        "semantic-scholar",
        "https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit={limit}&fields=title,abstract,authors,externalIds,publicationDate,url,openAccessPdf",
    )

    def _items(self, payload: object) -> list[Mapping[str, Any]]:
        return [x for x in payload.get("data", [])] if isinstance(payload, Mapping) else []

    def _parse(self, item: Mapping[str, Any], response: HttpResponse) -> Paper:
        external_value = item.get("externalIds")
        external: Mapping[str, Any] = (
            external_value if isinstance(external_value, Mapping) else {}
        )
        doi = _text(external.get("DOI")) or None
        stable = _text(item.get("paperId")) or doi or _text(item.get("url"))
        oa = item.get("openAccessPdf")
        return self._paper(
            stable_id=stable,
            item=item,
            response=response,
            title=_text(item.get("title")),
            abstract=_text(item.get("abstract")),
            authors=_authors(item.get("authors"), "name"),
            doi=doi,
            date=_text(item.get("publicationDate")) or None,
            url=_text(item.get("url")),
            access=AccessStatus.OPEN
            if isinstance(oa, Mapping) and oa.get("url")
            else AccessStatus.UNKNOWN,
        )


PROVIDERS = {
    "arxiv": ArxivAdapter,
    "openalex": OpenAlexAdapter,
    "crossref": CrossrefAdapter,
    "semantic-scholar": SemanticScholarAdapter,
}
