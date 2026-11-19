import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.keywords import Keyword
from app.models import (
    Base,
    Business,
    Conversation,
    ConversationStatus,
    Customer,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.consent import can_text
from app.services.inbound import InboundText, receive_text

BUSINESS_NUMBER = "+12015550100"
CUSTOMER = "+12015550123"


def add(session, row):
    session.add(row)
    session.flush()
    return row


def add_business(session, number=BUSINESS_NUMBER) -> Business:
    return add(session, Business(name="Shine Mobile Detailing", timezone="America/New_York", texting_number=number))


def inbound(**fields) -> InboundText:
    defaults = {
        "provider": "twilio",
        "account": "AC0001",
        "message_id": f"SM{uuid.uuid4().hex}",
        "sender": CUSTOMER,
        "recipient": BUSINESS_NUMBER,
        "body": "suv full detail, 07030",
    }
    return InboundText(**(defaults | fields))


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def revision(session, conversation_id) -> int:
    return session.scalar(select(Conversation.revision).where(Conversation.id == conversation_id))


@pytest.fixture
def business(session) -> Business:
    return add_business(session)


def test_first_text_starts_a_conversation(session, business):
    received = receive_text(session, inbound(body="suv full detail, dog hair in the back"))
    message = received.message
    conversation = session.get(Conversation, message.conversation_id)
    customer = session.get(Customer, conversation.customer_id)

    assert (received.duplicate, received.keyword) == (False, None)
    assert (message.direction, message.status) == (MessageDirection.INBOUND, MessageStatus.RECEIVED)
    assert message.body == "suv full detail, dog hair in the back"
    assert (customer.business_id, customer.phone) == (business.id, CUSTOMER)
    assert (conversation.business_id, conversation.status) == (business.id, ConversationStatus.ACTIVE)


def test_later_texts_join_the_open_conversation(session, business):
    first = receive_text(session, inbound()).message
    before = revision(session, first.conversation_id)

    second = receive_text(session, inbound(body="saturday?")).message

    assert second.conversation_id == first.conversation_id
    assert second.seq > first.seq
    # in-flight work based on the chat before this text is now stale
    assert revision(session, first.conversation_id) == before + 1


def test_redelivered_text_is_stored_once(session, business):
    text = inbound()
    first = receive_text(session, text)
    before = revision(session, first.message.conversation_id)

    again = receive_text(session, text)

    assert (again.duplicate, again.message.id) == (True, first.message.id)
    assert count(session, Message) == 1
    assert revision(session, first.message.conversation_id) == before


def test_same_words_with_a_new_id_are_a_new_text(session, business):
    receive_text(session, inbound(body="saturday?"))
    receive_text(session, inbound(body="saturday?"))

    assert count(session, Message) == 2


def test_text_to_an_unknown_number_is_refused(session, business):
    with pytest.raises(LookupError, match=r"no business texts from \+12015550999"):
        receive_text(session, inbound(recipient="+12015550999"))

    assert (count(session, Customer), count(session, Message)) == (0, 0)


def test_texts_go_to_the_business_whose_number_was_texted(session, business):
    other = add_business(session, number="+12015550200")

    here = receive_text(session, inbound()).message
    there = receive_text(session, inbound(recipient="+12015550200")).message

    assert (here.business_id, there.business_id) == (business.id, other.id)
    assert count(session, Customer) == 2


@pytest.mark.parametrize("sender", [BUSINESS_NUMBER, "+12015550200"], ids=["its own number", "another business"])
def test_texts_from_business_numbers_are_refused(session, business, sender):
    add_business(session, number="+12015550200")

    with pytest.raises(ValueError, match="is a business number"):
        receive_text(session, inbound(sender=sender))
    assert (count(session, Customer), count(session, Message)) == (0, 0)


def test_archived_conversation_stays_closed(session, business):
    first = receive_text(session, inbound()).message
    archived = session.get(Conversation, first.conversation_id)
    archived.status = ConversationStatus.ARCHIVED
    session.flush()

    later = receive_text(session, inbound()).message

    assert later.conversation_id != archived.id
    assert count(session, Customer) == 1


def test_stop_is_applied_on_arrival(session, business):
    stop = inbound(body="STOP")
    received = receive_text(session, stop)
    customer_id = session.get(Conversation, received.message.conversation_id).customer_id

    assert received.keyword == Keyword.OPT_OUT
    assert not can_text(session, customer_id)
    # a redelivered STOP was already handled
    assert receive_text(session, stop).keyword is None


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"body": "x" * 1601}, id="over twilio's limit"),
        pytest.param({"sender": "2015550123"}, id="sender not e164"),
        pytest.param({"sender": "SHINEAUTO"}, id="alphanumeric sender"),
        pytest.param({"recipient": "+1 201 555 0100"}, id="recipient with spaces"),
        pytest.param({"message_id": ""}, id="no message id"),
        pytest.param({"provider": "whatsapp"}, id="unknown provider"),
        pytest.param({"media_url": "https://example.com/a.jpg"}, id="unknown field"),
    ],
)
def test_malformed_texts_are_refused(fields):
    with pytest.raises(ValidationError):
        inbound(**fields)


def test_longest_text_twilio_allows_is_kept_whole(session, business):
    body = "x" * 1600

    assert receive_text(session, inbound(body=body)).message.body == body


@pytest.mark.parametrize(
    ("number", "constraint"),
    [(BUSINESS_NUMBER, "uq_business_texting_number"), ("201-555-0100", "ck_business_texting_number_e164")],
    ids=["number taken", "not e164"],
)
def test_business_numbers_are_unique_and_e164(session, business, number, constraint):
    with pytest.raises(IntegrityError) as caught:
        add_business(session, number=number)
    assert caught.value.orig.diag.constraint_name == constraint


def race(scratch_engine, wait_for_blocked_query, first_text, second_text):
    Base.metadata.create_all(scratch_engine)
    with Session(scratch_engine) as setup:
        add_business(setup)
        setup.commit()

    first, second = Session(scratch_engine), Session(scratch_engine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            receive_text(first, first_text)
            waiting = pool.submit(receive_text, second, second_text)
            wait_for_blocked_query()
            first.commit()
            later = waiting.result(timeout=10)
            second.commit()
        finally:
            first.close()
            second.close()
    return later


def test_first_texts_at_once_make_one_customer(scratch_engine, wait_for_blocked_query):
    later = race(scratch_engine, wait_for_blocked_query, inbound(), inbound(body="saturday?"))

    assert not later.duplicate
    with Session(scratch_engine) as check:
        assert (count(check, Customer), count(check, Conversation), count(check, Message)) == (1, 1, 2)


def test_one_text_delivered_twice_at_once_is_stored_once(scratch_engine, wait_for_blocked_query):
    text = inbound()

    later = race(scratch_engine, wait_for_blocked_query, text, text)

    assert later.duplicate
    with Session(scratch_engine) as check:
        assert count(check, Message) == 1
