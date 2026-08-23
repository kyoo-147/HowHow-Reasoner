import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from typer.testing import CliRunner

from howhow.cli import app
from howhow.contracts import (
    ID,
    Actor,
    BudgetReservation,
    Correlation,
    Decision,
    EventEnvelope,
    Hypothesis,
    PackageManifest,
    ProviderIdentity,
    RecordStatus,
    ResearchBrief,
    ReviewRecord,
    ReviewStatus,
    RunManifest,
    SourceRecord,
    TaskSpec,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def ident(name: str = "x") -> ID:
    return ID(value=name)


def actor() -> Actor:
    return Actor(actor_id=ident("human"), kind="human")


def provider() -> ProviderIdentity:
    return ProviderIdentity(
        provider_id="local-subprocess",
        provider_kind="runner",
        implementation="howhow",
        version="0.1",
    )


def test_invalid_version_and_naive_time_fail() -> None:
    with pytest.raises(ValueError):
        BudgetReservation(reservation_id=ident(), resource="cpu", limit=1, schema_version="v99")
    with pytest.raises(ValueError):
        Decision(
            decision_id=ident(),
            actor=actor(),
            choice="x",
            rationale="y",
            occurred_at=datetime(2026, 1, 1),
        )


def test_budget_and_provider_identity() -> None:
    with pytest.raises(ValueError):
        BudgetReservation(reservation_id=ident(), resource="cpu", limit=-1)
    assert provider().provider_id == "local-subprocess"


def test_status_serialization() -> None:
    h = Hypothesis(hypothesis_id=ident(), statement="x", falsifier="y")
    assert h.model_dump()["status"] == "PROPOSED"


def test_event_hash() -> None:
    payload = {"status": "ok"}
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    event = EventEnvelope(
        event_id=ident("event"),
        actor=actor(),
        event_type="created",
        aggregate_type="task",
        aggregate_id=ident("task"),
        correlation=Correlation(project_id=ident("project")),
        payload=payload,
        payload_sha256=digest,
        occurred_at=NOW,
    )
    assert EventEnvelope.model_validate_json(event.model_dump_json()).event_id.value == "event"


def test_cli_schema_export_is_stable(tmp_path) -> None:
    result = CliRunner().invoke(app, ["schema", "export", str(tmp_path)])
    assert result.exit_code == 0
    first = {p.name: p.read_text() for p in (tmp_path / "v1").glob("*")}
    CliRunner().invoke(app, ["schema", "export", str(tmp_path)])
    assert first == {p.name: p.read_text() for p in (tmp_path / "v1").glob("*")}


def test_record_family_roundtrips() -> None:
    records = [
        ResearchBrief(brief_id=ident(), project_id=ident("p"), question="q", occurred_at=NOW),
        TaskSpec(
            task_id=ident(),
            project_id=ident("p"),
            objective="o",
            provider=provider(),
            idempotency_key="k",
            occurred_at=NOW,
        ),
        SourceRecord(
            source_id=ident(),
            provider="arxiv",
            stable_locator="a",
            content_sha256="h",
            access="open",
            occurred_at=NOW,
        ),
        RunManifest(
            run_id=ident(),
            task_id=ident("t"),
            code_revision="r",
            command=["x"],
            status=RecordStatus.SUCCEEDED,
            occurred_at=NOW,
        ),
        ReviewRecord(
            review_id=ident(), reviewer=actor(), status=ReviewStatus.OPEN, occurred_at=NOW
        ),
        PackageManifest(package_id=ident(), artifacts=[], gates=[], occurred_at=NOW),
    ]
    assert all(type(type(r).model_validate_json(r.model_dump_json())) is type(r) for r in records)
