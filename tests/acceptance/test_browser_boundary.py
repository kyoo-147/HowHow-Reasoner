from pathlib import Path

from fastapi.testclient import TestClient

from howhow.api import create_app


def test_vite_origin_can_read_loopback_health(tmp_path: Path) -> None:
    client = TestClient(create_app(project_root=tmp_path))

    response = client.get("/health", headers={"Origin": "http://127.0.0.1:4173"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:4173"


def test_vite_origin_can_preflight_idempotent_writes(tmp_path: Path) -> None:
    client = TestClient(create_app(project_root=tmp_path))

    response = client.options(
        "/projects",
        headers={
            "Origin": "http://127.0.0.1:4173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:4173"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "Idempotency-Key" in response.headers["access-control-allow-headers"]
