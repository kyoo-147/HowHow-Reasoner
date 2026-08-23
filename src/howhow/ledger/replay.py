from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..project.layout import ProjectLayout, open_project
from .store import EventStore


@dataclass
class Projection:
    aggregates: dict[str, dict[str, dict[str, Any]]]

    def as_dict(self) -> dict[str, Any]:
        return {"aggregates": deepcopy(self.aggregates)}


def _apply(projection: Projection, event: Any) -> None:
    kind = event.aggregate_type
    ident = event.aggregate_id.value
    payload = deepcopy(event.payload)
    bucket = projection.aggregates.setdefault(kind, {})
    if event.event_type.lower() in {"deleted", "delete", "removed", "remove"}:
        bucket.pop(ident, None)
        return
    existing = bucket.setdefault(ident, {})
    if isinstance(payload.get("state"), dict):
        existing.update(payload["state"])
    else:
        existing.update(payload)
    existing["_event_id"] = event.event_id.value


def rebuild(project: ProjectLayout | Path) -> Projection:
    layout = project if isinstance(project, ProjectLayout) else open_project(project)
    projection = Projection(aggregates={})
    for event in EventStore(layout).read():
        _apply(projection, event)
    return projection


def write_projection(project: ProjectLayout | Path, projection: Projection) -> Path:
    layout = project if isinstance(project, ProjectLayout) else open_project(project)
    temp = layout.projection.with_suffix(".tmp")
    temp.write_text(
        json.dumps(projection.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(layout.projection)
    return layout.projection
