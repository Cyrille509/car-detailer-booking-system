from fastapi.testclient import TestClient

from app.main import create_app


def test_ready_returns_ok_with_database(engine):
    client = TestClient(create_app())

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
