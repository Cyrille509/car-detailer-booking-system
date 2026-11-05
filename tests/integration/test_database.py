from contextlib import closing

from sqlalchemy import text

from app.db import get_session


def test_session_runs_query(engine):
    with closing(get_session()) as sessions:
        session = next(sessions)

        assert session.execute(text("SELECT 1")).scalar_one() == 1
