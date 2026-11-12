import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.domain.pricing import PricingError
from app.models import Base, Business, Conversation, Customer, Estimate, EstimateStatus, JobRequest
from app.services.catalog import latest_catalog_version, publish_catalog
from app.services.estimates import ESTIMATE_VALIDITY, issue_estimate

OWNER = uuid.UUID("6f1c2a52-0d5e-4c1b-9a43-2e8f0b7d3c11")
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
SETTINGS = {
    "currency": "usd",
    "packages": {"full_detail": {"prices_minor": {"sedan": 15000, "suv": 18000}, "minutes": 180}},
    "add_ons": {"pet_hair": {"price_minor": 4000, "minutes": 45}},
    "zones": {
        "US": {
            "07030": {"fee_minor": 0, "tax_ppm": 0},
            "07310": {"fee_minor": 0, "tax_ppm": 0, "review": True},
        }
    },
    "deposit_bps": 2000,
    "lengths": [180, 240],
    "buffer_minutes": 30,
}
RAISED = SETTINGS | {"packages": {"full_detail": {"prices_minor": {"sedan": 15000, "suv": 20000}, "minutes": 180}}}


def add(session, row):
    session.add(row)
    session.flush()
    return row


def add_job(session, **fields) -> JobRequest:
    # the design doc's example: an SUV full detail with pet hair, in its own business
    business = add(session, Business(name="Shine Mobile Detailing", timezone="America/New_York"))
    customer = add(session, Customer(business_id=business.id, phone="+12015550123"))
    conversation = add(session, Conversation(business_id=business.id, customer_id=customer.id))
    details = {
        "country": "US",
        "postal_code": "07030",
        "vehicle_class": "suv",
        "package_id": "full_detail",
        "add_on_ids": ["pet_hair"],
    }
    return add(session, JobRequest(business_id=business.id, conversation_id=conversation.id, **(details | fields)))


def history(session, job) -> list[tuple[int, EstimateStatus]]:
    rows = select(Estimate.version, Estimate.status).where(Estimate.job_request_id == job.id)
    return [tuple(row) for row in session.execute(rows.order_by(Estimate.version))]


def valid_row(job, catalog_version_id, **changes) -> Estimate:
    fields = {
        "business_id": job.business_id,
        "job_request_id": job.id,
        "catalog_version_id": catalog_version_id,
        "version": 1,
        "status": EstimateStatus.ISSUED,
        "intake": {},
        "lines": [],
        "currency": "usd",
        "subtotal_minor": 22000,
        "tax_minor": 0,
        "total_minor": 22000,
        "deposit_minor": 4400,
        "event_minutes": 240,
        "buffer_minutes": 30,
        "issued_at": NOW,
        "expires_at": NOW + ESTIMATE_VALIDITY,
    }
    return Estimate(**(fields | changes))


@pytest.fixture
def job(session) -> JobRequest:
    job = add_job(session)
    publish_catalog(session, job.business_id, SETTINGS, OWNER)
    return job


def test_estimate_is_a_snapshot_of_the_quote(session, job):
    estimate = issue_estimate(session, job, NOW)
    session.expire_all()

    assert (estimate.version, estimate.status) == (1, EstimateStatus.ISSUED)
    assert estimate.lines == [
        {"kind": "PACKAGE", "ref": "full_detail", "amount_minor": 18000},
        {"kind": "ADD_ON", "ref": "pet_hair", "amount_minor": 4000},
    ]
    assert (estimate.total_minor, estimate.deposit_minor, estimate.event_minutes) == (22000, 4400, 240)
    assert estimate.intake["add_on_ids"] == ["pet_hair"]
    assert estimate.catalog_version_id == latest_catalog_version(session, job.business_id).id
    assert estimate.expires_at == NOW + timedelta(days=7)


def test_correction_closes_the_old_estimate(session, job):
    first = issue_estimate(session, job, NOW)
    # the customer drops pet hair
    job.add_on_ids = []
    session.flush()

    second = issue_estimate(session, job, NOW)

    assert history(session, job) == [(1, EstimateStatus.SUPERSEDED), (2, EstimateStatus.ISSUED)]
    assert (first.total_minor, second.total_minor) == (22000, 18000)


def test_correction_takes_back_an_acceptance(session, job):
    accepted = issue_estimate(session, job, NOW)
    accepted.status = EstimateStatus.ACCEPTED
    job.vehicle_class = "sedan"
    session.flush()

    issue_estimate(session, job, NOW)

    assert history(session, job) == [(1, EstimateStatus.SUPERSEDED), (2, EstimateStatus.ISSUED)]


def test_each_vehicle_gets_its_own_estimate(session, job):
    sedan = add(
        session,
        JobRequest(
            business_id=job.business_id,
            conversation_id=job.conversation_id,
            country="US",
            postal_code="07030",
            vehicle_class="sedan",
            package_id="full_detail",
        ),
    )

    suv_estimate = issue_estimate(session, job, NOW)
    sedan_estimate = issue_estimate(session, sedan, NOW)

    assert (suv_estimate.version, sedan_estimate.version) == (1, 1)
    assert (suv_estimate.total_minor, sedan_estimate.total_minor) == (22000, 15000)


def test_asking_again_keeps_the_same_estimate(session, job):
    first = issue_estimate(session, job, NOW)

    # a redelivered text, or the same question a few days later
    assert issue_estimate(session, job, NOW + timedelta(days=6)) is first
    assert history(session, job) == [(1, EstimateStatus.ISSUED)]


def test_new_prices_reach_only_new_estimates(session, job):
    issued = issue_estimate(session, job, NOW)
    publish_catalog(session, job.business_id, RAISED, OWNER)

    # still valid and unchanged, so the customer keeps the price they were given
    assert issue_estimate(session, job, NOW + timedelta(hours=1)) is issued
    job.add_on_ids = []
    session.flush()
    revised = issue_estimate(session, job, NOW + timedelta(hours=2))
    assert (issued.total_minor, revised.total_minor) == (22000, 20000)


def test_estimate_lasts_seven_days(session, job):
    first = issue_estimate(session, job, NOW)

    assert issue_estimate(session, job, NOW + ESTIMATE_VALIDITY - timedelta(microseconds=1)) is first
    renewed = issue_estimate(session, job, NOW + ESTIMATE_VALIDITY)
    assert history(session, job) == [(1, EstimateStatus.EXPIRED), (2, EstimateStatus.ISSUED)]
    assert renewed.expires_at == NOW + 2 * ESTIMATE_VALIDITY


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        pytest.param({"package_id": None}, "still needed: package_id", id="details missing"),
        pytest.param({"unresolved_scope": "headliner stain"}, "needs the owner", id="unpriced work"),
        pytest.param({"postal_code": "07310"}, "priced by the owner", id="zip the owner prices"),
        pytest.param({"vehicle_class": "pickup"}, "no price for pickup", id="class not offered"),
    ],
)
def test_work_that_cannot_be_priced_gets_no_estimate(session, fields, reason):
    job = add_job(session, **fields)
    publish_catalog(session, job.business_id, SETTINGS, OWNER)

    with pytest.raises(PricingError, match=reason):
        issue_estimate(session, job, NOW)
    assert history(session, job) == []


def test_no_estimate_before_the_owner_publishes_prices(session):
    with pytest.raises(PricingError, match="no published catalog"):
        issue_estimate(session, add_job(session), NOW)


def test_details_changed_elsewhere_are_not_priced(session, job):
    session.execute(text("UPDATE job_request SET revision = revision + 1 WHERE id = :id"), {"id": job.id})

    with pytest.raises(StaleDataError):
        issue_estimate(session, job, NOW)


def test_time_without_a_timezone_is_refused(session, job):
    with pytest.raises(ValueError, match="timezone"):
        issue_estimate(session, job, datetime(2026, 11, 11, 15, 0))


def test_only_the_status_moves_after_issue(session, job):
    estimate = issue_estimate(session, job, NOW)
    estimate.status = EstimateStatus.ACCEPTED
    session.flush()

    estimate.expires_at += timedelta(days=30)
    with pytest.raises(IntegrityError, match="estimate snapshots cannot be changed"):
        session.flush()


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        pytest.param({"tax_minor": -1, "total_minor": 21999}, "ck_estimate_amounts_not_negative", id="negative"),
        pytest.param({"total_minor": 21999}, "ck_estimate_total_adds_up", id="total off by a cent"),
        pytest.param({"deposit_minor": 0}, "ck_estimate_deposit_within_total", id="no deposit"),
        pytest.param({"deposit_minor": 22001}, "ck_estimate_deposit_within_total", id="deposit above total"),
        pytest.param({"expires_at": NOW}, "ck_estimate_expires_after_issue", id="expires when issued"),
        pytest.param({"version": 0}, "ck_estimate_version_positive", id="version zero"),
    ],
)
def test_database_refuses_inconsistent_estimates(session, job, changes, constraint):
    published = latest_catalog_version(session, job.business_id)

    with pytest.raises(IntegrityError) as caught:
        add(session, valid_row(job, published.id, **changes))
    assert caught.value.orig.diag.constraint_name == constraint


def test_database_allows_one_open_estimate_per_vehicle(session, job):
    issued = issue_estimate(session, job, NOW)

    with pytest.raises(IntegrityError) as caught:
        add(session, valid_row(job, issued.catalog_version_id, version=2))
    assert caught.value.orig.diag.constraint_name == "uq_estimate_open"


@pytest.mark.parametrize("borrowed", ["job_request", "catalog_version"])
def test_estimate_cannot_mix_businesses(session, job, borrowed):
    other_job = add_job(session)
    other_catalog = publish_catalog(session, other_job.business_id, SETTINGS, OWNER)
    own_catalog = latest_catalog_version(session, job.business_id)
    row = valid_row(job, own_catalog.id)
    if borrowed == "job_request":
        row.job_request_id = other_job.id
    else:
        row.catalog_version_id = other_catalog.id

    with pytest.raises(IntegrityError) as caught:
        add(session, row)
    assert caught.value.orig.diag.constraint_name == f"fk_estimate_business_id_{borrowed}"


def test_concurrent_requests_for_one_vehicle_share_one_estimate(scratch_engine, wait_for_blocked_query):
    Base.metadata.create_all(scratch_engine)
    with Session(scratch_engine) as setup:
        job_id = add_job(setup).id
        publish_catalog(setup, setup.get(JobRequest, job_id).business_id, SETTINGS, OWNER)
        setup.commit()

    first, second = Session(scratch_engine), Session(scratch_engine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            issued = issue_estimate(first, first.get(JobRequest, job_id), NOW)
            waiting = pool.submit(lambda: issue_estimate(second, second.get(JobRequest, job_id), NOW))
            wait_for_blocked_query()
            first.commit()
            # without the lock, both would issue version 1 and the second would fail
            assert waiting.result(timeout=10).id == issued.id
        finally:
            first.close()
    second.close()
