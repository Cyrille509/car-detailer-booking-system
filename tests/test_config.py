from pathlib import Path

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


def test_environment_is_required(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    with pytest.raises(ValidationError, match="environment"):
        Settings(_env_file=None)


def test_env_file_is_anchored_to_the_project_folder():
    env_file = Settings.model_config["env_file"]

    assert env_file.is_absolute()
    assert env_file == Path(__file__).resolve().parents[1] / ".env"
