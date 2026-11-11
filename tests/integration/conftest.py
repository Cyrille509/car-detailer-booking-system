import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.db import get_engine

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="session")
def engine() -> Engine:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except ValidationError as exc:
        pytest.fail(f"settings invalid (copy .env.example to .env): {exc}")
    except OperationalError as exc:
        pytest.fail(
            "postgres unavailable (run docker compose up -d, "
            f"or pass --ignore=tests/integration to run without it): {exc}"
        )
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


@pytest.fixture
def scratch_engine(engine: Engine) -> Iterator[Engine]:
    # an empty database of its own, for tests that commit or change the schema
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        admin.execute(text(f'CREATE DATABASE "{name}"'))
    scratch = create_engine(
        engine.url.set(database=name), poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    yield scratch
    scratch.dispose()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        admin.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
