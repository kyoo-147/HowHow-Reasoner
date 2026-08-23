"""Read-only scholarly adapters with normalized identities and bounded retrieval."""
# mypy: disable-error-code=no-untyped-def

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
class ProviderConfig:
    user_agent: str = "howhow/0.1"
    email: str | None = None
    retries: int = 2


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
    provider = ""
    endpoint = ""

    def __init__(
        self,
        *,
        transport: Transport = default_transport,
        cache: CacheHook | None = None,
        config: ProviderConfig | None = None,
    ) -> None:
        self.transport = transport
        self.cache = cache
        self.config = config or ProviderConfig()

    def search(self, query: str, *, limit: int = 10) -> tuple[Paper, ...]:
        if not query.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid query or limit")
        headers = {"Accept": "application/json", "User-Agent": self.config.user_agent}
        if self.config.email:
            headers["From"] = self.config.email
        payload, response = retrieve_json(
            self.endpoint.format(query=quote_plus(query), limit=limit),
            transport=self.transport,
            cache=self.cache,
            headers=headers,
            retries=self.config.retries,
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
        response: HttpResponse,
        title: str,
        abstract: str,
        authors: tuple[str, ...],
        doi: str | None = None,
        version: str | None = None,
        date: str | None = None,
        url: str | None = None,
        access: AccessStatus = AccessStatus.UNKNOWN,
        arxiv_id: str | None = None,
        openalex_id: str | None = None,
        semantic_scholar_id: str | None = None,
    ) -> Paper:
        source = SourceRecord(
            f"{self.provider}:{stable_id}",
            self.provider,
            stable_id,
            version,
            access,
            retrieved_now(),
            sha256(response.body).hexdigest(),
            title,
            url,
            abstract=abstract,
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=openalex_id,
            semantic_scholar_id=semantic_scholar_id,
        )
        return Paper(source, title, abstract, authors, doi, date, version)


def _text(v: object) -> str:
    return v if isinstance(v, str) else ""


def _authors(v: object, *keys: str) -> tuple[str, ...]:
    out = []
    for author in v if isinstance(v, list) else []:
        if isinstance(author, str):
            out.append(author)
        elif isinstance(author, Mapping):
            nested: Mapping[str, Any] = author
            author_value = author.get("author")
            if isinstance(author_value, Mapping):
                nested = author_value
            for key in keys:
                if isinstance(name := nested.get(key), str) and name:
                    out.append(name)
                    break
    return tuple(out)


class ArxivAdapter(LiteratureAdapter):
    provider = "arxiv"
    endpoint = "https://export.arxiv.org/api/query?search_query=all:{query}&max_results={limit}"

    def _items(self, payload):
        return (
            [x for x in payload.get("entries", []) if isinstance(x, Mapping)]
            if isinstance(payload, Mapping)
            else []
        )

    def _parse(self, item, response):
        url = _text(item.get("id"))
        stable = url.rstrip("/").rsplit("/", 1)[-1]
        return self._paper(
            stable_id=stable,
            response=response,
            title=_text(item.get("title")).strip(),
            abstract=_text(item.get("summary")).strip(),
            authors=_authors(item.get("authors"), "name"),
            version=_text(item.get("version")) or None,
            date=_text(item.get("published")) or None,
            url=url,
            access=AccessStatus.OPEN,
            arxiv_id=stable,
        )


class OpenAlexAdapter(LiteratureAdapter):
    provider = "openalex"
    endpoint = "https://api.openalex.org/works?search={query}&per-page={limit}"

    def _items(self, payload):
        return (
            [x for x in payload.get("results", []) if isinstance(x, Mapping)]
            if isinstance(payload, Mapping)
            else []
        )

    def _parse(self, item, response):
        url = _text(item.get("id"))
        stable = url.rstrip("/").rsplit("/", 1)[-1]
        doi = _text(item.get("doi")) or None
        open_access = item.get("open_access")
        is_open = isinstance(open_access, Mapping) and bool(open_access.get("is_oa"))
        return self._paper(
            stable_id=stable,
            response=response,
            title=_text(item.get("title")),
            abstract="",
            authors=_authors(item.get("authorships"), "display_name"),
            doi=doi,
            date=_text(item.get("publication_date")) or None,
            url=url,
            access=AccessStatus.OPEN if is_open else AccessStatus.UNKNOWN,
            openalex_id=stable,
        )


class CrossrefAdapter(LiteratureAdapter):
    provider = "crossref"
    endpoint = "https://api.crossref.org/works?query={query}&rows={limit}"

    def _items(self, payload):
        return (
            [x for x in payload.get("message", {}).get("items", []) if isinstance(x, Mapping)]
            if isinstance(payload, Mapping)
            else []
        )

    def _parse(self, item, response):
        doi = _text(item.get("DOI")) or None
        stable = doi or _text(item.get("URL"))
        titles = item.get("title")
        title = titles[0] if isinstance(titles, list) and titles else _text(titles)
        pub = item.get("published", {})
        date = _text(pub.get("date-time")) if isinstance(pub, Mapping) else ""
        return self._paper(
            stable_id=stable,
            response=response,
            title=title,
            abstract=_text(item.get("abstract")),
            authors=_authors(item.get("author"), "given", "family"),
            doi=doi,
            date=date or None,
            url=_text(item.get("URL")) or None,
        )


class SemanticScholarAdapter(LiteratureAdapter):
    provider = "semantic-scholar"
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit={limit}&fields=title,abstract,authors,externalIds,publicationDate,url,openAccessPdf"

    def _items(self, payload):
        return (
            [x for x in payload.get("data", []) if isinstance(x, Mapping)]
            if isinstance(payload, Mapping)
            else []
        )

    def _parse(self, item, response):
        external = item.get("externalIds") if isinstance(item.get("externalIds"), Mapping) else {}
        doi = _text(external.get("DOI")) or None
        stable = _text(item.get("paperId")) or doi or _text(item.get("url"))
        oa = item.get("openAccessPdf")
        return self._paper(
            stable_id=stable,
            response=response,
            title=_text(item.get("title")),
            abstract=_text(item.get("abstract")),
            authors=_authors(item.get("authors"), "name"),
            doi=doi,
            date=_text(item.get("publicationDate")) or None,
            url=_text(item.get("url")) or None,
            access=AccessStatus.OPEN
            if isinstance(oa, Mapping) and oa.get("url")
            else AccessStatus.UNKNOWN,
            semantic_scholar_id=stable,
        )


PROVIDERS = {
    "arxiv": ArxivAdapter,
    "openalex": OpenAlexAdapter,
    "crossref": CrossrefAdapter,
    "semantic-scholar": SemanticScholarAdapter,
}
