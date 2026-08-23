"""Safe, injectable HTTP retrieval primitives with explicit failure classes."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class RateClass(StrEnum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    INVALID_PAYLOAD = "invalid_payload"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponse: ...


class CacheHook(Protocol):
    def get(self, key: str) -> HttpResponse | None: ...
    def put(self, key: str, response: HttpResponse) -> None: ...


class RetrievalError(RuntimeError):
    def __init__(self, classification: RateClass, message: str) -> None:
        super().__init__(message)
        self.classification = classification


def classify_response(status: int) -> RateClass:
    if status == 429:
        return RateClass.RATE_LIMITED
    if 200 <= status < 300:
        return RateClass.OK
    if 400 <= status < 500:
        return RateClass.CLIENT_ERROR
    if status >= 500:
        return RateClass.SERVER_ERROR
    return RateClass.NETWORK_ERROR


def default_transport(url: str, headers: Mapping[str, str]) -> HttpResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return HttpResponse(response.status, response.read(), dict(response.headers.items()))
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, error.read(), dict(error.headers.items()))
    except OSError as error:
        raise RetrievalError(RateClass.NETWORK_ERROR, str(error)) from error


def retrieve_json(
    url: str,
    *,
    transport: Transport = default_transport,
    cache: CacheHook | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[object, HttpResponse]:
    key = url
    response = cache.get(key) if cache else None
    if response is None:
        response = transport(
            url, headers or {"Accept": "application/json", "User-Agent": "howhow/0.1"}
        )
        if cache:
            cache.put(key, response)
    classification = classify_response(response.status)
    if classification is not RateClass.OK:
        raise RetrievalError(classification, f"HTTP {response.status} for {url}")
    try:
        return json.loads(response.body.decode("utf-8")), response
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetrievalError(RateClass.INVALID_PAYLOAD, f"invalid JSON from {url}") from error


class MemoryCache:
    def __init__(self) -> None:
        self._items: dict[str, HttpResponse] = {}

    def get(self, key: str) -> HttpResponse | None:
        return self._items.get(key)

    def put(self, key: str, response: HttpResponse) -> None:
        self._items[key] = response
