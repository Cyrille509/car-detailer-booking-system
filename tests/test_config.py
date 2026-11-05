import pytest
from pydantic import ValidationError

from app.config import Settings

DATABASE_URL = "postgresql+psycopg://user:secret@db.example.com:5432/booking"


def test_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    settings = Settings(_env_file=None)

    assert settings.environment == "production"
    assert str(settings.database_url) == DATABASE_URL


def test_unknown_environment_is_rejected(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
