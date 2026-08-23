"""Bounded, injectable HTTP retrieval with truthful provider failures and raw caching."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
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

    @property
    def raw_sha256(self) -> str:
        return sha256(self.body).hexdigest()


class Transport(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponse: ...


class CacheHook(Protocol):
    def get(self, key: str) -> HttpResponse | None: ...
    def put(self, key: str, response: HttpResponse) -> None: ...


class RetrievalError(RuntimeError):
    def __init__(self, classification: RateClass, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.classification, self.attempts = classification, attempts


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
    retries: int = 2,
    backoff_seconds: float = 0.25,
) -> tuple[object, HttpResponse]:
    if retries < 0 or retries > 5:
        raise ValueError("retries must be between 0 and 5")
    key = url
    response = cache.get(key) if cache else None
    attempts = 0
    request_headers = {"Accept": "application/json", "User-Agent": "howhow/0.1"}
    if headers:
        request_headers.update(headers)
    while response is None:
        attempts += 1
        try:
            response = transport(url, request_headers)
        except RetrievalError as error:
            if error.classification is RateClass.NETWORK_ERROR and attempts <= retries:
                time.sleep(backoff_seconds * 2 ** (attempts - 1))
                continue
            raise RetrievalError(error.classification, str(error), attempts=attempts) from error
        classification = classify_response(response.status)
        if (
            classification in (RateClass.RATE_LIMITED, RateClass.SERVER_ERROR)
            and attempts <= retries
        ):
            retry_after = response.headers.get("Retry-After")
            try:
                delay = (
                    min(float(retry_after), 30.0)
                    if retry_after
                    else backoff_seconds * 2 ** (attempts - 1)
                )
            except ValueError:
                delay = backoff_seconds * 2 ** (attempts - 1)
            time.sleep(max(0.0, delay))
            response = None
            continue
        break
    assert response is not None
    if cache:
        cache.put(key, response)
    classification = classify_response(response.status)
    if classification is not RateClass.OK:
        raise RetrievalError(classification, f"HTTP {response.status} for {url}", attempts=attempts)
    try:
        return json.loads(response.body.decode("utf-8")), response
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetrievalError(
            RateClass.INVALID_PAYLOAD, f"invalid JSON from {url}", attempts=attempts
        ) from error


class MemoryCache:
    def __init__(self) -> None:
        self._items: dict[str, HttpResponse] = {}

    def get(self, key: str) -> HttpResponse | None:
        return self._items.get(key)

    def put(self, key: str, response: HttpResponse) -> None:
        self._items[key] = response


class FileCache:
    """Portable raw-response cache. Files contain data only and are never executed."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / (sha256(key.encode()).hexdigest() + ".json")

    def get(self, key: str) -> HttpResponse | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            return HttpResponse(
                int(item["status"]), bytes.fromhex(item["body_hex"]), item.get("headers", {})
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def put(self, key: str, response: HttpResponse) -> None:
        path = self._path(key)
        path.write_text(
            json.dumps(
                {
                    "url_sha256": sha256(key.encode()).hexdigest(),
                    "status": response.status,
                    "headers": dict(response.headers),
                    "body_sha256": response.raw_sha256,
                    "body_hex": response.body.hex(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
