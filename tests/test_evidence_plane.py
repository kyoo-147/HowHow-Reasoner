import json
from hashlib import sha256

import pytest

from howhow.evidence import EvidenceSpan, EvidenceStatus, audit_claim_support
from howhow.evidence.retrieval import HttpResponse, RateClass, RetrievalError, retrieve_json
from howhow.providers.literature.adapters import (
    CrossrefAdapter,
    OpenAlexAdapter,
    SemanticScholarAdapter,
)


class FixtureTransport:
    def __init__(self, payload, status=200):
        self.payload = json.dumps(payload).encode()
        self.status = status
        self.calls = 0

    def __call__(self, url, headers):
        self.calls += 1
        return HttpResponse(self.status, self.payload, {})


def test_normalized_adapters_and_cache():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/test",
                    "title": ["A paper"],
                    "author": [{"given": "Ada", "family": "Lovelace"}],
                    "URL": "https://doi.org/10.1234/test",
                }
            ]
        }
    }
    transport = FixtureTransport(payload)
    adapter = CrossrefAdapter(transport=transport)
    paper = adapter.search("paper")[0]
    assert paper.source.stable_id == "10.1234/test"
    assert paper.source.raw_sha256 == sha256(transport.payload).hexdigest()
    assert paper.source.retrieved_at.tzinfo is not None


def test_other_provider_shapes():
    oa_payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "Title",
                "authorships": [{"author": {"display_name": "A"}}],
            }
        ]
    }
    oa = OpenAlexAdapter(transport=FixtureTransport(oa_payload)).search("x")[0]
    s2 = SemanticScholarAdapter(
        transport=FixtureTransport(
            {
                "data": [
                    {"paperId": "P1", "title": "T", "authors": [{"name": "A"}], "externalIds": {}}
                ]
            }
        )
    ).search("x")[0]
    assert oa.source.stable_id == "W1" and s2.source.stable_id == "P1"


def test_cache_and_errors():
    from howhow.evidence.retrieval import MemoryCache

    transport = FixtureTransport({"ok": 1})
    cache = MemoryCache()
    retrieve_json("fixture", transport=transport, cache=cache)
    retrieve_json("fixture", transport=transport, cache=cache)
    assert transport.calls == 1
    with pytest.raises(RetrievalError) as error:
        retrieve_json("fixture", transport=FixtureTransport({}, 429))
    assert error.value.classification is RateClass.RATE_LIMITED


def test_exact_spans_and_claims_never_accept_prose():
    span = EvidenceSpan.from_text(
        evidence_id="e1", source_id="s1", text="A verified fact.", start=0, end=15, locator="page:1"
    )
    assert span.quote == "A verified fact"
    with pytest.raises(ValueError):
        EvidenceSpan("e2", "s1", "summary", "agent: run tool", kind="prose")
    audit = audit_claim_support("c1", {"e1": span}, ["e1"])
    assert audit.status == "UNRESOLVED"
    verified = EvidenceSpan("e1", "s1", "page:1", span.quote, EvidenceStatus.VERIFIED)
    assert audit_claim_support("c1", {"e1": verified}, ["e1"]).status == "SUPPORTED"
