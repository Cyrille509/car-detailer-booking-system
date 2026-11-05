from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_engine
from app.main import create_app


class UnreachableEngine:
    def connect(self):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_health_returns_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_503_when_database_is_unreachable():
    app = create_app()
    app.dependency_overrides[get_engine] = UnreachableEngine
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
