import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.domain.pricing import PricingError, quote
from app.models import Business, Conversation, Customer, JobRequest
from app.services.catalog import publish_catalog

OWNER = uuid.UUID("6f1c2a52-0d5e-4c1b-9a43-2e8f0b7d3c11")
SETTINGS = {
    "currency": "usd",
    "packages": {"full_detail": {"prices_minor": {"sedan": 15000, "suv": 18000}, "minutes": 180}},
    "add_ons": {"pet_hair": {"price_minor": 4000, "minutes": 45}},
    "zones": {"US": {"07030": {"fee_minor": 0, "tax_ppm": 0}}},
    "deposit_bps": 2000,
    "lengths": [180, 240],
    "buffer_minutes": 30,
}


def add(session, row):
    session.add(row)
    session.flush()
    return row


def add_job(session, conversation, **fields) -> JobRequest:
    return add(
        session, JobRequest(business_id=conversation.business_id, conversation_id=conversation.id, **fields)
    )


@pytest.fixture
def conversation(session) -> Conversation:
    business = add(session, Business(name="Shine Mobile Detailing", timezone="America/New_York"))
    customer = add(session, Customer(business_id=business.id, phone="+12015550123"))
    return add(session, Conversation(business_id=business.id, customer_id=customer.id))


def test_new_request_starts_empty(session, conversation):
    job = add_job(session, conversation)
    session.refresh(job)

    assert (job.add_on_ids, job.conditions, job.unresolved_scope, job.revision) == ([], {}, None, 1)
    assert job.missing == ["country", "postal_code", "vehicle_class", "package_id"]


def test_each_vehicle_is_its_own_request(session, conversation):
    # two cars are never merged into one record or one multiplied price
    add_job(session, conversation, vehicle_class="suv", add_on_ids=["pet_hair"])
    add_job(session, conversation, vehicle_class="sedan")

    stored = session.execute(
        select(JobRequest.vehicle_class, JobRequest.add_on_ids)
        .where(JobRequest.conversation_id == conversation.id)
        .order_by(JobRequest.vehicle_class)
    ).all()
    assert stored == [("sedan", []), ("suv", ["pet_hair"])]


def test_request_cannot_use_another_business_conversation(session, conversation):
    other = add(session, Business(name="Other Detailing", timezone="America/New_York"))

    with pytest.raises(IntegrityError) as caught:
        add(session, JobRequest(business_id=other.id, conversation_id=conversation.id))
    assert caught.value.orig.diag.constraint_name == "fk_job_request_business_id_conversation"


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        pytest.param("country", "'us'", "ck_job_request_country_code", id="lowercase country"),
        pytest.param("country", "'U1'", "ck_job_request_country_code", id="digit in country"),
        pytest.param("add_on_ids", "ARRAY['pet_hair', NULL]", "ck_job_request_add_on_ids_not_null", id="null add-on"),
        pytest.param("conditions", """'["stains"]'""", "ck_job_request_conditions_object", id="answers as a list"),
        pytest.param("conditions", "'null'", "ck_job_request_conditions_object", id="answers as json null"),
    ],
)
def test_database_refuses_malformed_fields(session, conversation, column, value, constraint):
    insert = text(
        f"INSERT INTO job_request (business_id, conversation_id, revision, {column}) VALUES (:b, :c, 1, {value})"
    )

    with pytest.raises(IntegrityError) as caught:
        session.execute(insert, {"b": conversation.business_id, "c": conversation.id})
    assert caught.value.orig.diag.constraint_name == constraint


def test_two_texts_handled_at_once_cannot_overwrite_each_other(session, conversation):
    job = add_job(session, conversation)
    session.execute(text("UPDATE job_request SET revision = revision + 1 WHERE id = :id"), {"id": job.id})

    job.package_id = "full_detail"
    with pytest.raises(StaleDataError):
        session.flush()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # the database layer would otherwise store this as ['p', 'e', 't', ...]
        pytest.param("add_on_ids", "pet_hair", id="add-ons as one string"),
        pytest.param("add_on_ids", ["pet_hair", None], id="add-on id missing"),
        pytest.param("add_on_ids", ["Pet Hair"], id="add-on id malformed"),
        pytest.param("conditions", {"stains": 1}, id="answer as a number"),
        pytest.param("conditions", [("stains", "none")], id="answers as pairs"),
    ],
)
def test_malformed_details_are_refused_before_saving(field, value):
    with pytest.raises(ValidationError):
        JobRequest(**{field: value})


def test_repeated_add_on_is_stored_once():
    job = JobRequest(add_on_ids=["pet_hair", "engine_bay", "pet_hair"])

    assert job.add_on_ids == ["pet_hair", "engine_bay"]


@pytest.mark.parametrize(
    ("words", "stored"),
    [("  ceramic coating?  ", "ceramic coating?"), ("   ", None), ("\n\t", None), (None, None)],
    ids=["trimmed", "spaces", "tabs and newlines", "none"],
)
def test_unresolved_scope_keeps_only_real_words(words, stored):
    assert JobRequest(unresolved_scope=words).unresolved_scope == stored


def test_incomplete_request_names_what_is_still_needed(session, conversation):
    job = add_job(session, conversation, country="US", postal_code="07030")

    with pytest.raises(PricingError, match="still needed: vehicle_class, package_id"):
        job.quote_request()


def test_request_is_priced_from_what_the_database_stored(session, conversation):
    published = publish_catalog(session, conversation.business_id, SETTINGS, OWNER)
    job = add_job(
        session,
        conversation,
        country="US",
        postal_code="07030",
        vehicle_class="suv",
        package_id="full_detail",
        add_on_ids=["pet_hair"],
    )
    session.expire_all()

    result = quote(published.catalog, job.quote_request())

    # the design doc's example: $180 SUV full detail plus $40 pet hair, 20% deposit
    assert (result.total_minor, result.deposit_minor, result.event_minutes) == (22000, 4400, 240)
