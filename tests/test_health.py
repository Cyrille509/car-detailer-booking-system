import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import get_engine
from app.main import create_app


class UnreachableEngine:
    def connect(self):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


@pytest.fixture
def valid_settings(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@db.example.com:5432/booking")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


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


def test_app_starts_with_valid_settings(valid_settings):
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    ("name", "value"),
    [("DATABASE_URL", "not-a-url"), ("ENVIRONMENT", "staging")],
)
def test_app_refuses_to_start_with_bad_settings(valid_settings, name, value):
    valid_settings.setenv(name, value)

    with pytest.raises(ValidationError), TestClient(create_app()):
        pass
