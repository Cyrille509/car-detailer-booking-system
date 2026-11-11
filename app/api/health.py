from functools import lru_cache
from pathlib import Path
from typing import Annotated

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_engine

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

router = APIRouter()


@lru_cache
def expected_revisions() -> frozenset[str]:
    return frozenset(ScriptDirectory(str(MIGRATIONS)).get_heads())


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(engine: Annotated[Engine, Depends(get_engine)]) -> dict[str, str]:
    try:
        with engine.connect() as connection:
            current = frozenset(MigrationContext.configure(connection).get_current_heads())
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="database unavailable") from None
    # a reachable database on an old or missing schema would fail every real request
    if current != expected_revisions():
        raise HTTPException(status_code=503, detail="database schema mismatch")
    return {"status": "ok"}
