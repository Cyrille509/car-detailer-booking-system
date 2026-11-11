from app.models import Business


def test_business_gets_id_and_created_at(session):
    business = Business(name="Shine Mobile Detailing", timezone="America/New_York")
    session.add(business)
    session.flush()
    session.refresh(business)

    assert business.id is not None
    assert business.created_at.tzinfo is not None
