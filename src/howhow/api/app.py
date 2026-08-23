from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from howhow.contracts import ID, Actor, ApprovalRecord, Correlation, EventEnvelope
from howhow.ledger import EventStore, rebuild
from howhow.project import ProjectError, ProjectLayout, init_project, open_project
from howhow.providers.registry import ProviderRegistry

MAX_BODY_BYTES = 1_048_576


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(StrictModel):
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    name: str | None = None


class BriefProposal(StrictModel):
    question: str = Field(min_length=1, max_length=20_000)
    scope: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)


class ApprovalInput(StrictModel):
    scope: str = Field(min_length=1, max_length=1_000)
    actor_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="human", min_length=1, max_length=100)


class TaskInput(StrictModel):
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    objective: str = Field(min_length=1, max_length=20_000)
    provider_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=300)
    approval_id: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> ID:
    return ID(value=f"{prefix}:{secrets.token_hex(8)}")


def _event(
    project_id: str,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> EventEnvelope:
    return EventEnvelope(
        event_id=_id("event"),
        actor=Actor(actor_id=ID(value="api"), kind="control-plane"),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=ID(value=aggregate_id),
        correlation=Correlation(project_id=ID(value=project_id)),
        payload=payload,
        payload_sha256=hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        occurred_at=_now(),
    )


def create_app(
    *, project_root: Path | None = None, max_body_bytes: int = MAX_BODY_BYTES
) -> FastAPI:
    root = (project_root or Path.cwd()).expanduser().resolve()
    registry = ProviderRegistry()
    app = FastAPI(title="HowHow Control Plane", version="v1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "Idempotency-Key"],
    )
    app.state.project_root = root
    app.state.registry = registry
    app.state.max_body_bytes = max_body_bytes

    @app.middleware("http")
    async def request_limit(request: Request, call_next: Any) -> Any:
        length = request.headers.get("content-length")
        if length and int(length) > max_body_bytes:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=413, content={"detail": "request body exceeds configured limit"}
            )
        return await call_next(request)

    def layout(project_id: str) -> ProjectLayout:
        candidate = (root / project_id).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=400, detail="project path escapes configured root")
        try:
            return open_project(candidate)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def append_once(
        project_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> EventEnvelope:
        store = EventStore(layout(project_id))
        events = store.read()
        if idempotency_key:
            for existing in events:
                if existing.payload.get("idempotency_key") == idempotency_key:
                    return existing
            payload = {**payload, "idempotency_key": idempotency_key}
        return store.append(_event(project_id, aggregate_type, aggregate_id, event_type, payload))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "howhow-control-plane", "loopback_default": True}

    @app.get("/readiness")
    def readiness() -> dict[str, Any]:
        providers = [item.model_dump(mode="json") for item in registry.readiness()]
        return {
            "status": "ready"
            if all("READY" in item["status"] for item in providers)
            else "degraded",
            "providers": providers,
        }

    @app.get("/providers/capabilities")
    def capabilities() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in registry.capabilities()]

    @app.post("/projects")
    def create_project(body: CreateProject) -> dict[str, Any]:
        candidate = (root / body.project_id).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=400, detail="project path escapes configured root")
        try:
            result = init_project(candidate, project_id=body.project_id, name=body.name)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"project_id": body.project_id, "root": str(result.root)}

    @app.get("/projects/{project_id}/status")
    def project_status(project_id: str) -> dict[str, Any]:
        current = layout(project_id)
        projection = rebuild(current).as_dict()
        metadata = json.loads(current.metadata.read_text(encoding="utf-8"))
        return {
            "project": metadata,
            "projection": projection,
            "event_count": len(EventStore(current).read()),
        }

    @app.get("/projects/{project_id}/events")
    def events(project_id: str, after: int = Query(0, ge=0)) -> list[dict[str, Any]]:
        return [
            event.model_dump(mode="json") for event in EventStore(layout(project_id)).read()[after:]
        ]

    @app.post("/projects/{project_id}/rebuild")
    def rebuild_project(project_id: str) -> dict[str, Any]:
        current = layout(project_id)
        projection = rebuild(current)
        current.projection.write_text(
            json.dumps(projection.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return {"projection": projection.as_dict(), "event_count": len(EventStore(current).read())}

    @app.post("/projects/{project_id}/briefs")
    def propose_brief(project_id: str, body: BriefProposal, request: Request) -> dict[str, Any]:
        event = append_once(
            project_id,
            "brief",
            _id("brief").value,
            "brief_proposed",
            body.model_dump(),
            request.headers.get("Idempotency-Key"),
        )
        return {"event": event.model_dump(mode="json"), "status": "PROPOSED"}

    @app.post("/projects/{project_id}/approvals")
    def approve(project_id: str, body: ApprovalInput, request: Request) -> dict[str, Any]:
        approval = ApprovalRecord(
            approval_id=_id("approval"),
            actor=Actor(actor_id=ID(value=body.actor_id), kind=body.kind),
            scope=body.scope,
            occurred_at=_now(),
        )
        event = append_once(
            project_id,
            "approval",
            approval.approval_id.value,
            "approval_recorded",
            approval.model_dump(mode="json"),
            request.headers.get("Idempotency-Key"),
        )
        return {
            "approval": approval.model_dump(mode="json"),
            "event": event.model_dump(mode="json"),
        }

    @app.post("/projects/{project_id}/tasks")
    def create_task(project_id: str, body: TaskInput, request: Request) -> dict[str, Any]:
        if not body.approval_id:
            raise HTTPException(status_code=403, detail="approved action is required")
        events = EventStore(layout(project_id)).read()
        if not any(
            e.aggregate_id.value == body.approval_id and e.event_type == "approval_recorded"
            for e in events
        ):
            raise HTTPException(status_code=403, detail="approval was not found")
        event = append_once(
            project_id,
            "task",
            body.task_id,
            "task_created",
            body.model_dump(),
            body.idempotency_key,
        )
        return {"task": body.model_dump(), "event": event.model_dump(mode="json")}

    @app.get("/projects/{project_id}/evidence/audit")
    def evidence_audit(project_id: str) -> dict[str, Any]:
        events = EventStore(layout(project_id)).read()
        return {
            "events": len(events),
            "verified_chain": True,
            "evidence": [
                e.model_dump(mode="json")
                for e in events
                if e.aggregate_type in {"evidence", "source"}
            ],
        }

    @app.get("/projects/{project_id}/stream")
    def stream(project_id: str, after: int = Query(0, ge=0)) -> StreamingResponse:
        events = EventStore(layout(project_id)).read()[after:]
        body = "".join(
            f"id: {after + index + 1}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
            for index, event in enumerate(events)
        )
        return StreamingResponse(iter([body]), media_type="text/event-stream")

    @app.websocket("/projects/{project_id}/stream/ws")
    async def stream_ws(websocket: WebSocket, project_id: str) -> None:
        await websocket.accept()
        try:
            after = int(websocket.query_params.get("after", "0"))
            for event in EventStore(layout(project_id)).read()[after:]:
                await websocket.send_json(event.model_dump(mode="json"))
            await websocket.close()
        except (WebSocketDisconnect, ValueError):
            return

    return app


app = create_app()
