from datetime import UTC, datetime
from hashlib import sha256

import pytest

from howhow.contracts import ID, Actor, Correlation, EventEnvelope
from howhow.ledger import (
    ArtifactError,
    ArtifactStore,
    EventStore,
    LockError,
    TruncatedTailError,
    rebuild,
)
from howhow.project import init_project


def event(number: int, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope(
        event_id=ID(value=f"e{number}"),
        actor=Actor(actor_id=ID(value="test"), kind="test"),
        event_type="updated",
        aggregate_type="task",
        aggregate_id=ID(value="t1"),
        correlation=Correlation(project_id=ID(value="p1")),
        payload=payload,
        payload_sha256=sha256(
            __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_append_verify_and_idempotent_replay(tmp_path) -> None:
    project = init_project(tmp_path / "p", project_id="p1")
    store = EventStore(project)
    store.append(event(1, {"status": "new"}))
    store.append(event(2, {"status": "done"}))
    assert store.verify() == 2
    first = rebuild(project).as_dict()
    assert first == rebuild(project).as_dict()
    assert first["aggregates"]["task"]["t1"]["status"] == "done"


def test_lock_and_truncated_tail(tmp_path) -> None:
    project = init_project(tmp_path / "p", project_id="p1")
    store = EventStore(project)
    project.lock.write_text("held")
    with pytest.raises(LockError):
        store.append(event(1, {}))
    project.lock.unlink()
    store.append(event(1, {}))
    with project.events.open("ab") as handle:
        handle.write(b"{")
    with pytest.raises(TruncatedTailError):
        store.verify()


def test_artifact_hash_mismatch_and_promote(tmp_path) -> None:
    project = init_project(tmp_path / "p", project_id="p1")
    source = tmp_path / "data"
    source.write_bytes(b"hello")
    artifacts = ArtifactStore(project)
    with pytest.raises(ArtifactError):
        artifacts.stage(source, "0" * 64)
    digest = artifacts.stage(source)
    target = artifacts.promote(digest)
    assert target.read_bytes() == b"hello"
