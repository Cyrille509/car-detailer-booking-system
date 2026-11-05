import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from app.db import get_engine


@pytest.fixture(scope="session")
def engine() -> Engine:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except (ValidationError, OperationalError) as exc:
        pytest.skip(f"postgres unavailable (copy .env.example to .env, run docker compose up -d): {exc}")
    return engine
