import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, Business, CatalogVersion, Customer
from app.services.catalog import latest_catalog_version, publish_catalog

OWNER = uuid.UUID("6f1c2a52-0d5e-4c1b-9a43-2e8f0b7d3c11")
SETTINGS = {
    "currency": "usd",
    "packages": {"full_detail": {"prices_minor": {"suv": 18000}, "minutes": 180}},
    "add_ons": {"pet_hair": {"price_minor": 4000, "minutes": 45}},
    "zones": {"US": {"07030": {"fee_minor": 0, "tax_ppm": 0}}},
    "deposit_bps": 2000,
    "lengths": [180, 240],
    "buffer_minutes": 30,
}
RAISED = SETTINGS | {"packages": {"full_detail": {"prices_minor": {"suv": 20000}, "minutes": 180}}}


def add_business(session) -> Business:
    business = Business(name="Shine Mobile Detailing", timezone="America/New_York")
    session.add(business)
    session.flush()
    return business


def suv_price(version: CatalogVersion) -> int:
    return version.catalog.packages["full_detail"].prices_minor["suv"]


def count_versions(session, business) -> int:
    return session.scalar(
        select(func.count()).select_from(CatalogVersion).where(CatalogVersion.business_id == business.id)
    )


@pytest.fixture
def business(session):
    return add_business(session)


def test_versions_count_up_within_each_business(session, business):
    other = add_business(session)

    first = publish_catalog(session, business.id, SETTINGS, OWNER)
    second = publish_catalog(session, business.id, RAISED, OWNER)

    assert (first.version, second.version) == (1, 2)
    assert publish_catalog(session, other.id, SETTINGS, OWNER).version == 1


def test_new_prices_leave_older_versions_alone(session, business):
    # the owner raises prices mid-chat; anything priced from version 1 keeps its numbers
    old = publish_catalog(session, business.id, SETTINGS, OWNER)
    publish_catalog(session, business.id, RAISED, OWNER)
    session.expire_all()

    latest = latest_catalog_version(session, business.id)
    assert (old.version, suv_price(old)) == (1, 18000)
    assert (latest.version, suv_price(latest)) == (2, 20000)


def test_publishing_the_same_settings_again_is_not_a_new_version(session, business):
    first = publish_catalog(session, business.id, SETTINGS, OWNER)
    # same settings, written differently
    again = SETTINGS | {"lengths": [240, 180, 180], "zones": {"us": {" 07030": {"fee_minor": 0, "tax_ppm": 0}}}}

    assert publish_catalog(session, business.id, again, uuid.uuid4()) is first
    assert count_versions(session, business) == 1


def test_going_back_to_older_prices_is_a_new_version(session, business):
    for settings in (SETTINGS, RAISED, SETTINGS):
        latest = publish_catalog(session, business.id, settings, OWNER)

    assert (latest.version, suv_price(latest)) == (3, 18000)


def test_latest_version_comes_from_the_business_asked_about(session, business):
    publish_catalog(session, add_business(session).id, SETTINGS, OWNER)

    assert latest_catalog_version(session, business.id) is None


def test_invalid_settings_are_never_stored(session, business):
    with pytest.raises(ValidationError, match="deposit_bps"):
        publish_catalog(session, business.id, SETTINGS | {"deposit_bps": 0}, OWNER)

    assert count_versions(session, business) == 0


def test_unknown_business_is_refused(session):
    with pytest.raises(LookupError, match="unknown business"):
        publish_catalog(session, uuid.uuid4(), SETTINGS, OWNER)


def test_version_records_who_published_it_and_when(session, business):
    published = publish_catalog(session, business.id, SETTINGS, OWNER)
    session.refresh(published)

    assert published.published_by == OWNER
    assert published.published_at.tzinfo is not None


def test_published_versions_cannot_be_edited(session, business):
    published = publish_catalog(session, business.id, SETTINGS, OWNER)
    published.content = RAISED

    with pytest.raises(IntegrityError, match="catalog_version rows cannot be changed"):
        session.flush()


@pytest.mark.parametrize(
    ("version", "constraint"),
    [(0, "ck_catalog_version_version_positive"), (1, "uq_catalog_version_business_id_version")],
    ids=["zero", "taken"],
)
def test_database_refuses_bad_version_numbers(session, business, version, constraint):
    publish_catalog(session, business.id, SETTINGS, OWNER)
    session.add(CatalogVersion(business_id=business.id, version=version, content=SETTINGS, published_by=OWNER))

    with pytest.raises(IntegrityError) as caught:
        session.flush()
    assert caught.value.orig.diag.constraint_name == constraint


def wait_for_blocked_query(engine, seconds=5.0):
    # autocommit, because postgres keeps one activity snapshot per transaction
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            blocked = watcher.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
            if blocked:
                return
            time.sleep(0.02)
    raise AssertionError("second publish never waited")


@pytest.fixture
def committed_business_id(scratch_engine) -> uuid.UUID:
    Base.metadata.create_all(scratch_engine)
    with Session(scratch_engine) as setup:
        business_id = add_business(setup).id
        setup.commit()
    return business_id


def test_concurrent_publishes_take_turns(scratch_engine, committed_business_id):
    business_id = committed_business_id
    first, second = Session(scratch_engine), Session(scratch_engine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            publish_catalog(first, business_id, SETTINGS, OWNER)
            waiting = pool.submit(publish_catalog, second, business_id, RAISED, OWNER)
            wait_for_blocked_query(scratch_engine)
            first.commit()
            # without the lock, the second publish would also pick version 1 and fail
            assert waiting.result(timeout=10).version == 2
        finally:
            first.close()
    second.close()


def test_publishing_does_not_hold_up_new_customers(scratch_engine, committed_business_id):
    with Session(scratch_engine) as publishing, Session(scratch_engine) as texting:
        publish_catalog(publishing, committed_business_id, SETTINGS, OWNER)
        texting.execute(text("SET lock_timeout = '1s'"))

        # a full row lock on the business would make this insert time out
        texting.add(Customer(business_id=committed_business_id, phone="+12015550123"))
        texting.flush()
