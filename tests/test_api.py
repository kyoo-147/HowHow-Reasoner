from pathlib import Path

from fastapi.testclient import TestClient

from howhow.api import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(project_root=tmp_path, max_body_bytes=256))


def test_control_plane_happy_path_and_rebuild(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.get("/health").json()["loopback_default"] is True
    assert api.post("/projects", json={"project_id": "p1"}).status_code == 200
    first = api.post(
        "/projects/p1/briefs",
        json={"question": "find evidence"},
        headers={"Idempotency-Key": "brief-1"},
    )
    duplicate = api.post(
        "/projects/p1/briefs",
        json={"question": "find evidence"},
        headers={"Idempotency-Key": "brief-1"},
    )
    assert first.json()["event"]["event_id"] == duplicate.json()["event"]["event_id"]
    approval = api.post("/projects/p1/approvals", json={"scope": "run", "actor_id": "human"}).json()
    approval_id = approval["approval"]["approval_id"]["value"]
    assert (
        api.post(
            "/projects/p1/tasks",
            json={
                "task_id": "t1",
                "objective": "bounded",
                "provider_id": "local-subprocess",
                "idempotency_key": "task-1",
                "approval_id": approval_id,
            },
        ).status_code
        == 200
    )
    before = api.get("/projects/p1/status").json()["projection"]
    rebuilt = api.post("/projects/p1/rebuild").json()["projection"]
    assert before == rebuilt


def test_safety_guards_and_stream_resume(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.post("/projects", json={"project_id": "p1"}).status_code == 200
    assert api.post("/projects/../escape/briefs", json={"question": "x"}).status_code in {400, 404}
    assert (
        api.post(
            "/projects/p1/tasks",
            json={
                "task_id": "t",
                "objective": "x",
                "provider_id": "local-subprocess",
                "idempotency_key": "k",
            },
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/projects/p1/briefs", content=b"x" * 300, headers={"content-type": "application/json"}
        ).status_code
        == 413
    )
    api.post("/projects/p1/briefs", json={"question": "x"})
    stream = api.get("/projects/p1/stream?after=0")
    assert stream.status_code == 200 and "brief_proposed" in stream.text
    assert api.get("/projects/p1/events?after=1").json() == []
    assert api.get("/readiness").json()["providers"][0]["status"].startswith("READY_")
