from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.models import Business

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_business_gets_id_and_created_at(engine):
    command.upgrade(Config(toml_file=PYPROJECT), "head")

    with Session(engine) as session:
        business = Business(name="Shine Mobile Detailing", timezone="America/New_York")
        session.add(business)
        session.flush()
        session.refresh(business)

        assert business.id is not None
        assert business.created_at.tzinfo is not None
        session.rollback()
