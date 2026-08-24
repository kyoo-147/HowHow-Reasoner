"""Verify v2.1 artifact hashes against clean Git index blobs.

The manifest hashes canonical LF bytes from the Git index, not the platform
working-tree representation. This makes verification stable across CRLF/LF
worktrees while still rejecting dirty content, deletions, and mode changes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "episodes/harth-calibration/protocol/v2.1-artifact-hashes.json"
EXPECTED_MODE = "100644"


def canonical_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def index_bytes(relative: str) -> bytes:
    return _git("show", f":{relative}")


def index_mode(relative: str) -> str:
    rows = _git("ls-files", "--stage", "--", relative).decode("utf-8").splitlines()
    if len(rows) != 1:
        raise SystemExit(f"manifest path is missing or duplicated in index: {relative}")
    mode, _rest = rows[0].split(" ", 1)
    return mode


def assert_clean(relative: str) -> None:
    dirty = _git("status", "--porcelain=v1", "--untracked-files=no", "--", relative)
    if dirty:
        raise SystemExit(
            f"manifest path is dirty in worktree: {relative}: {dirty.decode().strip()}"
        )
    if index_mode(relative) != EXPECTED_MODE:
        raise SystemExit(f"manifest path has unsupported mode: {relative}")


def verify_manifest() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("hash_scope") != "canonical LF bytes of Git index blobs":
        raise SystemExit("manifest hash_scope is not the verifier contract")
    for relative, expected in manifest["hashes"].items():
        assert_clean(relative)
        actual = hashlib.sha256(canonical_lf(index_bytes(relative))).hexdigest()
        if actual != expected:
            raise SystemExit(f"manifest mismatch: {relative}: {actual} != {expected}")
    print(f"verified {len(manifest['hashes'])} canonical Git-index artifact hashes")
    return 0


def main() -> int:
    return verify_manifest()


if __name__ == "__main__":
    raise SystemExit(main())
