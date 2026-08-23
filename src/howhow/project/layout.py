from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProjectError(RuntimeError):
    """A project cannot be opened or initialized safely."""


@dataclass(frozen=True)
class ProjectLayout:
    root: Path

    @property
    def metadata(self) -> Path:
        return self.root / "project.json"

    @property
    def events(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def lock(self) -> Path:
        return self.root / "events.lock"

    @property
    def projection(self) -> Path:
        return self.root / "projection.json"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def staging(self) -> Path:
        return self.artifacts / "staging"

    @property
    def objects(self) -> Path:
        return self.artifacts / "objects"

    def ensure(self) -> None:
        if not self.metadata.is_file() or not self.events.is_file():
            raise ProjectError(f"not a HowHow project: {self.root}")


def _fsync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def init_project(root: Path, *, project_id: str, name: str | None = None) -> ProjectLayout:
    """Create a project atomically; never partially initialize an existing path."""
    root = root.expanduser().resolve()
    if root.exists():
        if any(root.iterdir()):
            raise ProjectError(f"project path already exists and is not empty: {root}")
    else:
        root.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        (temp / "artifacts" / "staging").mkdir(parents=True)
        (temp / "artifacts" / "objects").mkdir()
        metadata: dict[str, Any] = {
            "format": "howhow-project-v1",
            "project_id": project_id,
            "name": name or project_id,
        }
        (temp / "project.json").write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        (temp / "events.jsonl").touch()
        (temp / "projection.json").write_text("{}\n", encoding="utf-8")
        for child in (temp / "project.json", temp / "events.jsonl", temp / "projection.json"):
            with child.open("rb+") as handle:
                os.fsync(handle.fileno())
        if root.exists():
            root.rmdir()
        os.replace(temp, root)
        _fsync_dir(root.parent)
    except Exception:
        import shutil

        shutil.rmtree(temp, ignore_errors=True)
        raise
    return ProjectLayout(root)


def open_project(root: Path) -> ProjectLayout:
    layout = ProjectLayout(root.expanduser().resolve())
    layout.ensure()
    return layout
