from pathlib import Path

from howhow.workflows import EpisodeWorkflow, FixtureDemo, WorkflowState


def prepared(tmp_path: Path) -> EpisodeWorkflow:
    workflow = EpisodeWorkflow.create(tmp_path / "episode", project_id="p1", question="q")
    workflow.approve()
    workflow.plan()
    return workflow


def test_approval_is_required_and_restart_replays_ledger(tmp_path: Path) -> None:
    workflow = EpisodeWorkflow.create(tmp_path / "episode", project_id="p1", question="q")
    assert workflow.snapshot.state == WorkflowState.BRIEFING
    workflow.approve()
    workflow.plan()
    restarted = EpisodeWorkflow(tmp_path / "episode")
    assert restarted.snapshot.state == WorkflowState.LITERATURE
    assert len(restarted.snapshot.tasks) == 4


def test_provider_budget_and_lease_fail_closed(tmp_path: Path) -> None:
    workflow = prepared(tmp_path)
    workflow.complete_task("literature", provider_ready=False)
    assert workflow.snapshot.state == WorkflowState.PAUSED

    workflow = prepared(tmp_path / "second")
    workflow.complete_task("literature", lease_valid=False)
    assert workflow.snapshot.state == WorkflowState.BLOCKED

    workflow = prepared(tmp_path / "third")
    workflow.complete_task("literature", cost=2, budget_limit=1)
    assert workflow.snapshot.state == WorkflowState.BLOCKED


def test_failed_and_inconclusive_results_are_preserved(tmp_path: Path) -> None:
    workflow = prepared(tmp_path)
    workflow.complete_task("literature", status="FAILED", finding="baseline unavailable")
    assert workflow.snapshot.state == WorkflowState.FAILED

    workflow = prepared(tmp_path / "second")
    workflow.complete_task("literature", status="INCONCLUSIVE", finding="provider degraded")
    assert workflow.snapshot.state == WorkflowState.INCONCLUSIVE


def test_fixture_demo_stops_at_human_review(tmp_path: Path) -> None:
    snapshot = FixtureDemo.run(tmp_path / "fixture")
    assert snapshot.state == WorkflowState.READY_FOR_HUMAN_REVIEW
    assert all("fixture" in task for task in snapshot.tasks)
