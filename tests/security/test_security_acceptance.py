from __future__ import annotations

import threading
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from howhow.api import create_app
from howhow.contracts import ID, Actor, Correlation, EventEnvelope
from howhow.ledger import ArtifactError, ArtifactStore, EventStore
from howhow.project import init_project


def _event(number: int) -> EventEnvelope:
    payload = {"number": number, "idempotency_key": "race"}
    return EventEnvelope(
        event_id=ID(value=f"event:{number}"),
        actor=Actor(actor_id=ID(value="test"), kind="test"),
        event_type="test",
        aggregate_type="task",
        aggregate_id=ID(value="task:1"),
        correlation=Correlation(project_id=ID(value="p1")),
        payload=payload,
        payload_sha256=sha256(b'{"idempotency_key":"race","number":%d}' % number).hexdigest(),
        occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


def test_idempotency_is_atomic_under_concurrent_writers(tmp_path: Path) -> None:
    project = init_project(tmp_path / "p", project_id="p1")
    store = EventStore(project)
    results: list[EventEnvelope] = []
    errors: list[Exception] = []

    def append(number: int) -> None:
        try:
            results.append(store.append_idempotent(_event(number), "race"))
        except Exception as exc:  # pragma: no cover - assertion below reports failures
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(number,)) for number in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(errors) == 1
    assert len(results) == 1
    assert len(store.read()) == 1


def test_api_rejects_malformed_length_and_reports_corrupt_chain(tmp_path: Path) -> None:
    api = TestClient(create_app(project_root=tmp_path, max_body_bytes=256))
    assert api.post("/projects", json={"project_id": "p1"}).status_code == 200
    response = api.post(
        "/projects/p1/briefs",
        content=b'{"question":"x"}',
        headers={"content-type": "application/json", "content-length": "nope"},
    )
    assert response.status_code == 400
    project = tmp_path / "p1"
    project.joinpath("events.jsonl").write_bytes(b"{}\n")
    audit = api.get("/projects/p1/evidence/audit")
    assert audit.status_code == 200
    assert audit.json()["verified_chain"] is False


def test_project_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires the Windows privilege unavailable in this runner")
    api = TestClient(create_app(project_root=tmp_path))
    response = api.post("/projects", json={"project_id": "link/p1"})
    assert response.status_code == 400
    assert not (outside / "p1").exists()


def test_artifact_digest_must_be_a_path_safe_sha256(tmp_path: Path) -> None:
    project = init_project(tmp_path / "p", project_id="p1")
    artifacts = ArtifactStore(project)
    for value in ("../escape", "A" * 64, "0" * 63):
        try:
            artifacts.promote(value)
        except ArtifactError:
            pass
        else:  # pragma: no cover
            raise AssertionError("invalid digest was accepted")
