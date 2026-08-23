from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..project.layout import ProjectLayout, open_project


class ArtifactError(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, project: ProjectLayout | Path):
        self.project = project if isinstance(project, ProjectLayout) else open_project(project)
        self.project.staging.mkdir(parents=True, exist_ok=True)
        self.project.objects.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _validate_digest(digest: str) -> None:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ArtifactError(f"invalid artifact digest: {digest}")

    def stage(self, source: Path, expected_sha256: str | None = None) -> str:
        data = source.read_bytes()
        digest = self.digest(data)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ArtifactError(f"artifact hash mismatch: expected {expected_sha256}, got {digest}")
        target = self.project.staging / digest
        if not target.exists():
            temp = target.with_suffix(f".{os.getpid()}.tmp")
            temp.write_bytes(data)
            with temp.open("rb+") as handle:
                os.fsync(handle.fileno())
            os.replace(temp, target)
        return digest

    def promote(self, digest: str) -> Path:
        self._validate_digest(digest)
        source = self.project.staging / digest
        if not source.is_file():
            raise ArtifactError(f"staged artifact not found: {digest}")
        if self.digest(source.read_bytes()) != digest:
            raise ArtifactError(f"artifact hash mismatch: {digest}")
        target = self.project.objects / digest
        if target.exists():
            if self.digest(target.read_bytes()) != digest:
                raise ArtifactError(f"promoted artifact hash mismatch: {digest}")
            source.unlink()
        else:
            os.replace(source, target)
        return target

    def import_file(self, source: Path, expected_sha256: str | None = None) -> Path:
        return self.promote(self.stage(source, expected_sha256))
