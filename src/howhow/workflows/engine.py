from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

from howhow.contracts import (
    ID,
    Actor,
    ApprovalRecord,
    Correlation,
    EventEnvelope,
    Lifecycle,
    ProviderIdentity,
    ResearchBrief,
    TaskSpec,
)
from howhow.gates import (
    CompileCheck,
    LicenseManifest,
    Outcome,
    PackageInput,
    ReproducibilityManifest,
    RunRecord,
    StatisticalPlan,
    evaluate_readiness,
)
from howhow.ledger import EventStore, rebuild
from howhow.project import ProjectLayout, init_project, open_project


class EpisodeWorkflowError(RuntimeError):
    pass


class WorkflowState(StrEnum):
    BRIEFING = Lifecycle.BRIEFING
    WAITING_FOR_HUMAN = Lifecycle.WAITING_FOR_HUMAN
    LITERATURE = Lifecycle.LITERATURE
    BASELINE = Lifecycle.BASELINE
    REVIEW = Lifecycle.REVIEW
    PACKAGING = Lifecycle.PACKAGING
    READY_FOR_HUMAN_REVIEW = Lifecycle.READY_FOR_HUMAN_REVIEW
    PAUSED = Lifecycle.PAUSED
    BLOCKED = Lifecycle.BLOCKED
    FAILED = Lifecycle.FAILED
    INCONCLUSIVE = Lifecycle.INCONCLUSIVE


@dataclass(frozen=True)
class WorkflowSnapshot:
    project_id: str
    state: WorkflowState
    question: str
    tasks: tuple[str, ...]
    completed: tuple[str, ...]
    findings: tuple[str, ...] = ()


def _now() -> datetime:
    return datetime.now(UTC)


def _id(value: str) -> ID:
    return ID(value=value)


def _payload_hash(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EpisodeWorkflow:
    """Small orchestration facade over the existing event ledger.

    The ledger is authoritative: a new instance reconstructs its snapshot from
    events and never trusts an optimistic in-memory state. Provider execution
    is intentionally represented by task events; this class does not fabricate
    external results.
    """

    def __init__(self, project: ProjectLayout | Path, *, actor: str = "workflow") -> None:
        self.project = project if isinstance(project, ProjectLayout) else open_project(project)
        self.store = EventStore(self.project)
        self.actor = Actor(actor_id=_id(actor), kind="system")
        self._events = self.store.read()

    @classmethod
    def create(
        cls, root: Path, *, project_id: str, question: str, actor: str = "workflow"
    ) -> EpisodeWorkflow:
        project = init_project(root, project_id=project_id, name=question)
        workflow = cls(project, actor=actor)
        workflow._append(
            "project.created", "project", project_id, {"state": WorkflowState.BRIEFING.value}
        )
        brief = ResearchBrief(
            brief_id=_id(f"{project_id}/brief"),
            project_id=_id(project_id),
            question=question,
            occurred_at=_now(),
            lifecycle=Lifecycle.BRIEFING,
        )
        workflow._append(
            "brief.proposed",
            "brief",
            brief.brief_id.value,
            {"state": brief.model_dump(mode="json")},
        )
        workflow._refresh()
        return workflow

    def _refresh(self) -> None:
        self._events = self.store.read()

    def _append(
        self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict[str, Any]
    ) -> None:
        event = EventEnvelope(
            event_id=_id(f"{aggregate_id}/event-{len(self._events) + 1}"),
            actor=self.actor,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=_id(aggregate_id),
            correlation=Correlation(project_id=_id(self.project_id)),
            payload=payload,
            payload_sha256=_payload_hash(payload),
            occurred_at=_now(),
        )
        self.store.append(event)
        self._refresh()

    @property
    def project_id(self) -> str:
        from typing import cast

        return cast(
            str, json.loads(self.project.metadata.read_text(encoding="utf-8"))["project_id"]
        )

    @property
    def snapshot(self) -> WorkflowSnapshot:
        projection = rebuild(self.project).aggregates
        project = projection.get("project", {}).get(self.project_id, {})
        brief = projection.get("brief", {}).get(f"{self.project_id}/brief", {})
        task_bucket = projection.get("task", {})
        completed = tuple(
            sorted(k for k, v in task_bucket.items() if v.get("status") == "SUCCEEDED")
        )
        state = WorkflowState(project.get("state", WorkflowState.BRIEFING.value))
        return WorkflowSnapshot(
            self.project_id,
            state,
            brief.get("question", ""),
            tuple(sorted(task_bucket)),
            completed,
            tuple(project.get("findings", [])),
        )

    def _require(self, *states: WorkflowState) -> None:
        if self.snapshot.state not in states:
            raise EpisodeWorkflowError(
                "transition requires "
                f"{', '.join(s.value for s in states)}; "
                f"current={self.snapshot.state.value}"
            )

    def _set_state(self, state: WorkflowState, **extra: Any) -> None:
        self._append(
            "workflow.transition", "project", self.project_id, {"state": state.value, **extra}
        )

    def approve(self, *, actor: str = "human", expires_hours: float = 24.0) -> None:
        self._require(WorkflowState.BRIEFING, WorkflowState.WAITING_FOR_HUMAN)
        approval = ApprovalRecord(
            approval_id=_id(f"{self.project_id}/approval"),
            actor=Actor(actor_id=_id(actor), kind="human"),
            scope="research-episode",
            occurred_at=_now(),
            expires_at=_now() + timedelta(hours=expires_hours),
        )
        self._append(
            "brief.approved",
            "approval",
            approval.approval_id.value,
            {
                "state": approval.model_dump(mode="json"),
                "project_state": WorkflowState.LITERATURE.value,
            },
        )
        self._set_state(WorkflowState.LITERATURE)

    def deny(self, *, actor: str = "human", reason: str = "approval denied") -> None:
        self._require(WorkflowState.BRIEFING, WorkflowState.WAITING_FOR_HUMAN)
        self._append(
            "brief.denied",
            "project",
            self.project_id,
            {"state": WorkflowState.BLOCKED.value, "findings": [reason], "actor": actor},
        )
        self._set_state(WorkflowState.BLOCKED, findings=[reason])

    def plan(self, *, provider: ProviderIdentity | None = None) -> tuple[TaskSpec, ...]:
        self._require(WorkflowState.LITERATURE)
        provider = provider or ProviderIdentity(
            provider_id="episode-provider",
            provider_kind="task",
            implementation="external-adapter",
            version="v1",
        )
        tasks = tuple(
            TaskSpec(
                task_id=_id(f"{self.project_id}/{kind}"),
                project_id=_id(self.project_id),
                objective=kind,
                provider=provider,
                idempotency_key=f"{self.project_id}:{kind}:v1",
                occurred_at=_now(),
            )
            for kind in ("literature", "baseline", "review", "package")
        )
        for task in tasks:
            if task.task_id.value not in self.snapshot.tasks:
                self._append(
                    "task.planned",
                    "task",
                    task.task_id.value,
                    {"state": task.model_dump(mode="json")},
                )
        return tasks

    def complete_task(
        self,
        kind: str,
        *,
        status: str = "SUCCEEDED",
        finding: str | None = None,
        provider_ready: bool = True,
        lease_valid: bool = True,
        cost: float = 0.0,
        budget_limit: float = 1.0,
    ) -> None:
        task_id = f"{self.project_id}/{kind}"
        if task_id not in self.snapshot.tasks:
            raise EpisodeWorkflowError(f"task not planned: {kind}")
        if any(
            e.event_type == "task.completed" and e.aggregate_id.value == task_id
            for e in self._events
        ):
            return
        if not provider_ready:
            self._set_state(
                WorkflowState.PAUSED, findings=["provider degraded; retry requires readiness"]
            )
            return
        if not lease_valid:
            self._set_state(WorkflowState.BLOCKED, findings=["stale lease rejected result"])
            return
        if cost > budget_limit:
            self._set_state(WorkflowState.BLOCKED, findings=["budget exhausted"])
            return
        next_state = {
            "literature": WorkflowState.BASELINE,
            "baseline": WorkflowState.REVIEW,
            "review": WorkflowState.PACKAGING,
            "package": WorkflowState.READY_FOR_HUMAN_REVIEW,
        }.get(kind)
        if status != "SUCCEEDED":
            next_state = (
                WorkflowState.INCONCLUSIVE if status == "INCONCLUSIVE" else WorkflowState.FAILED
            )
        payload: dict[str, Any] = {
            "status": status,
            "state": next_state.value if next_state else WorkflowState.FAILED.value,
        }
        if finding:
            payload["finding"] = finding
        self._append("task.completed", "task", task_id, payload)
        self._set_state(next_state or WorkflowState.FAILED, findings=[finding] if finding else [])

    def package(self, package: PackageInput) -> bool:
        self._require(WorkflowState.PACKAGING)
        report = evaluate_readiness(package)
        if not report.ready:
            self._set_state(WorkflowState.BLOCKED, findings=list(report.findings))
            return False
        self.complete_task("package")
        return True


class FixtureDemo:
    """Deterministic demo driver; every output is explicitly labelled FIXTURE."""

    @staticmethod
    def run(root: Path, *, project_id: str = "fixture-episode") -> WorkflowSnapshot:
        workflow = EpisodeWorkflow.create(
            root, project_id=project_id, question="FIXTURE: deterministic research episode"
        )
        workflow.approve()
        workflow.plan()
        for kind in ("literature", "baseline", "review"):
            workflow.complete_task(kind)
        source = "FIXTURE measured effect 0.5."
        from howhow.gates import ArtifactLineage, CitationSpan, ClaimEvidence
        from howhow.review import ReviewerRecord, ReviewStatus

        base = PackageInput(
            claims=(
                ClaimEvidence(
                    claim_id="fixture-claim",
                    wording=source,
                    citations=(
                        CitationSpan(
                            evidence_id="fixture-evidence",
                            source_id="fixture-source",
                            locator="fixture:1",
                            quote=source,
                            start=0,
                            end=len(source),
                            source_text=source,
                        ),
                    ),
                ),
            ),
            lineage=(ArtifactLineage(artifact_id="fixture-artifact", run_id="fixture-run"),),
            runs=(
                RunRecord(
                    run_id="fixture-run", status="SUCCEEDED", artifact_ids=("fixture-artifact",)
                ),
            ),
            statistical_plan=StatisticalPlan(content="FIXTURE plan"),
            reproducibility=ReproducibilityManifest(code_revision="FIXTURE", command=("fixture",)),
            licenses=LicenseManifest(entries=("FIXTURE",)),
            compile_check=CompileCheck(passed=True, command="fixture"),
            outcome=Outcome.INCONCLUSIVE,
        )
        from howhow.gates import package_fingerprint

        base = base.model_copy(
            update={
                "reviewer_records": (
                    ReviewerRecord(
                        review_id="fixture-review",
                        reviewer_id="fixture",
                        input_fingerprint=package_fingerprint(base),
                        status=ReviewStatus.ACCEPTED,
                    ),
                )
            }
        )
        workflow.package(base)
        return workflow.snapshot
