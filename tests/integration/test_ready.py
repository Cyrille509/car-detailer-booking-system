import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.db import get_engine
from app.main import create_app


def get_ready(engine):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app).get("/ready")


@pytest.fixture
def scratch_engine(engine):
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


def test_ready_returns_ok_when_migrated(migrated_engine):
    response = get_ready(migrated_engine)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("revision", [None, "3f8a1c2d9e47"], ids=["no tables", "old revision"])
def test_ready_returns_503_when_not_migrated(scratch_engine, revision):
    if revision:
        with scratch_engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision})

    response = get_ready(scratch_engine)

    assert response.status_code == 503
    assert response.json() == {"detail": "database schema mismatch"}
