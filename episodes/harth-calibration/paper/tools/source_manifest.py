"""Canonical UTF-8/LF hashing for the public paper source manifest."""

from __future__ import annotations

import hashlib

HASH_MODE = "utf-8-lf-sha256-v1"


def canonical_utf8_lf(data: bytes) -> bytes:
    """Normalize text to strict UTF-8 with LF-only line endings."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is not permitted in canonical source files")
    text = data.decode("utf-8", errors="strict")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_sha256(data: bytes) -> str:
    return hashlib.sha256(canonical_utf8_lf(data)).hexdigest()

def verify_manifest_hashes(root, manifest_path):
    """Verify a manifest's file hashes using the canonical byte contract."""
    import json

    manifest_bytes = canonical_utf8_lf(manifest_path.read_bytes())
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("hash_mode") != HASH_MODE:
        raise ValueError("source manifest hash mode mismatch")
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict):
        raise ValueError("source manifest hashes are missing")
    for relative, expected in hashes.items():
        path = root / relative
        if not path.is_file() or canonical_sha256(path.read_bytes()) != expected:
            raise ValueError(f"source manifest file hash mismatch: {relative}")
    return manifest
