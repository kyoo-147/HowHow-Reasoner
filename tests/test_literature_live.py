import json
from pathlib import Path

from howhow.evidence.retrieval import FileCache, HttpResponse, RateClass, retrieve_json
from howhow.providers.literature.adapters import CrossrefAdapter, ProviderConfig


class SequenceTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.headers = []

    def __call__(self, url, headers):
        self.calls += 1
        self.headers.append(headers)
        return self.responses.pop(0)


def test_retry_after_and_provider_identity_headers():
    transport = SequenceTransport(
        [
            HttpResponse(429, b"{}", {"Retry-After": "0"}),
            HttpResponse(200, json.dumps({"message": {"items": []}}).encode(), {}),
        ]
    )
    adapter = CrossrefAdapter(
        transport=transport,
        config=ProviderConfig(user_agent="test-agent", email="research@example.org", retries=1),
    )
    assert adapter.search("HARTH", limit=1) == ()
    assert transport.calls == 2
    assert transport.headers[0]["User-Agent"] == "test-agent"
    assert transport.headers[0]["From"] == "research@example.org"


def test_file_cache_preserves_raw_hash(tmp_path: Path):
    cache = FileCache(tmp_path)
    response = HttpResponse(200, b'{"safe":"data"}', {"Content-Type": "application/json"})
    cache.put("https://fixture.invalid", response)
    restored = cache.get("https://fixture.invalid")
    assert restored is not None
    assert restored.body == response.body
    assert restored.raw_sha256 == response.raw_sha256


def test_network_failure_is_truthful():
    transport = SequenceTransport([HttpResponse(503, b"busy", {})])
    try:
        retrieve_json("fixture", transport=transport, retries=0)
    except Exception as error:
        assert error.classification is RateClass.SERVER_ERROR
    else:
        raise AssertionError("server failure must not be reported as a successful payload")
