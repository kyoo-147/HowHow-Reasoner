"""Resumable, checksum-verified HARTH downloader; raw data is never overwritten."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

UCI_HARTH_URL = "https://archive.ics.uci.edu/static/public/779/harth.zip"


@dataclass(frozen=True)
class DownloadManifest:
    url: str
    destination: str
    sha256: str
    bytes: int
    retrieved_at_utc: str
    source: str = "UCI Machine Learning Repository dataset 779"
    license: str = "CC BY 4.0"
    doi: str = "10.24432/C5NC90"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_harth(destination: Path, *, expected_sha256: str | None = None) -> DownloadManifest:
    """Download to ``.part``, resume with HTTP Range, then atomically publish.

    Existing destination files are immutable: a matching checksum returns its
    manifest, while a mismatch raises instead of replacing user data.
    """
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        digest = sha256_file(destination)
        if expected_sha256 and digest != expected_sha256:
            raise ValueError("existing HARTH archive checksum mismatch; refusing overwrite")
        return DownloadManifest(
            UCI_HARTH_URL, str(destination), digest, destination.stat().st_size, "existing"
        )
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(UCI_HARTH_URL)
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        partial.open("ab" if offset else "wb") as output,
    ):
        if offset and response.headers.get("Content-Range") is None:
            output.close()
            partial.unlink()
            return download_harth(destination, expected_sha256=expected_sha256)
        shutil.copyfileobj(response, output, length=1024 * 1024)
    digest = sha256_file(partial)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"HARTH checksum mismatch: expected {expected_sha256}, got {digest}")
    partial.replace(destination)
    manifest = DownloadManifest(
        UCI_HARTH_URL,
        str(destination),
        digest,
        destination.stat().st_size,
        datetime.now(UTC).isoformat(),
    )
    destination.with_suffix(destination.suffix + ".manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
    )
    return manifest
