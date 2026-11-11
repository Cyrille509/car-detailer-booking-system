from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_engine

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="session")
def engine() -> Engine:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except (ValidationError, OperationalError) as exc:
        pytest.skip(f"postgres unavailable (copy .env.example to .env, run docker compose up -d): {exc}")
    return engine


@pytest.fixture(scope="session")
def migrated_engine(engine: Engine) -> Engine:
    command.upgrade(Config(toml_file=PYPROJECT), "head")
    return engine


@pytest.fixture
def session(migrated_engine: Engine) -> Iterator[Session]:
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        # savepoint per test, so a constraint error doesn't break the outer rollback
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()
