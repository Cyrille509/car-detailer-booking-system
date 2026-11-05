from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        str(get_settings().database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def get_session() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session
