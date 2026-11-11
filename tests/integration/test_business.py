import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models import Business

TIMEZONE = "America/New_York"


def test_business_gets_id_and_created_at(session):
    business = Business(name="Shine Mobile Detailing", timezone=TIMEZONE)
    session.add(business)
    session.flush()
    session.refresh(business)

    assert business.id is not None
    assert business.created_at.tzinfo is not None


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError, match="unknown timezone"):
        Business(name="Shine Mobile Detailing", timezone="Not/AZone")


def test_name_is_trimmed():
    assert Business(name="  Shine Mobile Detailing  ", timezone=TIMEZONE).name == "Shine Mobile Detailing"


@pytest.mark.parametrize(
    "name", ["", "   ", "\t\n", "​"], ids=["empty", "spaces", "tabs and newlines", "zero-width space"]
)
def test_blank_name_is_rejected(name):
    with pytest.raises(ValueError, match="business name is blank"):
        Business(name=name, timezone=TIMEZONE)


def test_database_rejects_blank_name_written_directly(session):
    with pytest.raises(IntegrityError):
        session.execute(text("INSERT INTO business (name, timezone) VALUES ('   ', :tz)"), {"tz": TIMEZONE})


def test_name_up_to_200_characters_fits(session):
    session.add(Business(name="x" * 200, timezone=TIMEZONE))
    session.flush()

    session.add(Business(name="x" * 201, timezone=TIMEZONE))
    with pytest.raises(DataError):
        session.flush()
