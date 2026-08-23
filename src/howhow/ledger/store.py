from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..contracts import EventEnvelope
from ..project.layout import ProjectLayout, open_project


class LedgerError(RuntimeError):
    pass


class LockError(LedgerError):
    pass


class ChainError(LedgerError):
    pass


class TruncatedTailError(ChainError):
    pass


def canonical_event(event: EventEnvelope) -> bytes:
    return (
        json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def event_hash(event: EventEnvelope) -> str:
    import hashlib

    return hashlib.sha256(canonical_event(event)).hexdigest()


class EventStore:
    def __init__(self, project: ProjectLayout | Path):
        self.project = project if isinstance(project, ProjectLayout) else open_project(project)
        self.project.events.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _writer(self) -> Iterator[None]:
        try:
            fd = os.open(self.project.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise LockError(f"ledger is already locked: {self.project.lock}") from exc
        try:
            os.write(fd, f"pid={os.getpid()}\n".encode())
            os.fsync(fd)
            yield
        finally:
            os.close(fd)
            try:
                self.project.lock.unlink()
            except FileNotFoundError:
                pass

    def read(self) -> list[EventEnvelope]:
        if not self.project.events.exists():
            return []
        raw = self.project.events.read_bytes()
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise TruncatedTailError("ledger has a truncated final record")
        events: list[EventEnvelope] = []
        previous: str | None = None
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                event = EventEnvelope.model_validate_json(line)
            except Exception as exc:
                raise ChainError(f"invalid event at line {number}: {exc}") from exc
            if event.previous_event_sha256 != previous:
                raise ChainError(f"previous hash mismatch at line {number}")
            events.append(event)
            previous = event_hash(event)
        return events

    def verify(self) -> int:
        return len(self.read())

    def append_idempotent(self, event: EventEnvelope, key: str) -> EventEnvelope:
        """Append once for ``key`` while holding the writer lock."""
        if not key:
            raise ValueError("idempotency key must not be empty")
        with self._writer():
            events = self.read()
            for existing in events:
                if existing.payload.get("idempotency_key") == key:
                    return existing
            payload = {**event.payload, "idempotency_key": key}
            payload_sha256 = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            event = event.model_copy(update={"payload": payload, "payload_sha256": payload_sha256})
            previous = event_hash(events[-1]) if events else None
            event = event.model_copy(update={"previous_event_sha256": previous})
            with self.project.events.open("ab") as handle:
                handle.write(canonical_event(event))
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                fd = os.open(self.project.events.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return event

    def append(self, event: EventEnvelope) -> EventEnvelope:
        with self._writer():
            events = self.read()
            previous = event_hash(events[-1]) if events else None
            if event.previous_event_sha256 not in (None, previous):
                raise ChainError("event previous hash does not match ledger tail")
            if event.previous_event_sha256 != previous:
                event = event.model_copy(update={"previous_event_sha256": previous})
            line = canonical_event(event)
            with self.project.events.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                fd = os.open(self.project.events.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return event
