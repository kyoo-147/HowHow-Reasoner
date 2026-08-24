"""Verify v2.1 artifact hashes against clean Git index blobs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

EXPECTED_MODE = "100644"


def project_root() -> Path:
    """Return the repository root containing this source checkout."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("could not locate project root")


def canonical_lf(data: bytes) -> bytes:
    """Normalize all supported line endings to LF bytes."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout


def index_bytes(relative: str, *, root: Path | None = None) -> bytes:
    return _git(root or project_root(), "show", f":{relative}")


def index_mode(relative: str, *, root: Path | None = None) -> str:
    repo = root or project_root()
    rows = _git(repo, "ls-files", "--stage", "--", relative).decode("utf-8").splitlines()
    if len(rows) != 1:
        raise SystemExit(f"manifest path is missing or duplicated in index: {relative}")
    mode, _rest = rows[0].split(" ", 1)
    return mode


def assert_clean(relative: str, *, root: Path | None = None) -> None:
    repo = root or project_root()
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=no", "--", relative)
    if dirty:
        raise SystemExit(
            f"manifest path is dirty in worktree: {relative}: {dirty.decode().strip()}"
        )
    if index_mode(relative, root=repo) != EXPECTED_MODE:
        raise SystemExit(f"manifest path has unsupported mode: {relative}")


def verify_manifest(*, root: Path | None = None) -> int:
    """Verify the tracked v2.1 artifact hashes for a project checkout."""
    repo = root or project_root()
    manifest_path = repo / "episodes/harth-calibration/protocol/v2.1-artifact-hashes.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("hash_scope") != "canonical LF bytes of Git index blobs":
        raise SystemExit("manifest hash_scope is not the verifier contract")
    for relative, expected in manifest["hashes"].items():
        assert_clean(relative, root=repo)
        actual = hashlib.sha256(canonical_lf(index_bytes(relative, root=repo))).hexdigest()
        if actual != expected:
            raise SystemExit(f"manifest mismatch: {relative}: {actual} != {expected}")
    print(f"verified {len(manifest['hashes'])} canonical Git-index artifact hashes")
    return 0


def main() -> int:
    return verify_manifest()
