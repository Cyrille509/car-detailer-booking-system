import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models import (
    Business,
    Conversation,
    ConversationStatus,
    Customer,
    Message,
    MessageDirection,
    MessageStatus,
)


def add(session, row):
    session.add(row)
    session.flush()
    return row


def add_business(session):
    return add(session, Business(name="Shine Mobile Detailing", timezone="America/New_York"))


def add_customer(session, business, phone="+12015550123"):
    return add(session, Customer(business_id=business.id, phone=phone))


def add_conversation(session, customer, **fields):
    return add(session, Conversation(business_id=customer.business_id, customer_id=customer.id, **fields))


def add_message(session, conversation, **fields):
    defaults = {
        "business_id": conversation.business_id,
        "conversation_id": conversation.id,
        "direction": MessageDirection.INBOUND,
        "status": MessageStatus.RECEIVED,
        "provider": "twilio",
        "provider_account": "AC0001",
        "body": "suv full detail, dog hair in the back",
    }
    return add(session, Message(**(defaults | fields)))


@pytest.fixture
def customer(session):
    return add_customer(session, add_business(session))


@pytest.fixture
def conversation(session, customer):
    return add_conversation(session, customer)


def test_phone_is_unique_within_a_business(session, customer):
    add_customer(session, add_business(session), phone=customer.phone)

    with pytest.raises(IntegrityError):
        add(session, Customer(business_id=customer.business_id, phone=customer.phone))


def test_phone_must_be_e164(session):
    with pytest.raises(IntegrityError):
        add_customer(session, add_business(session), phone="201-555-0123")


def test_conversation_cannot_use_another_business_customer(session, customer):
    other = add_business(session)

    with pytest.raises(IntegrityError):
        add(session, Conversation(business_id=other.id, customer_id=customer.id))


def test_customer_has_one_open_conversation(session, customer):
    add_conversation(session, customer, status=ConversationStatus.ARCHIVED)
    add_conversation(session, customer)

    with pytest.raises(IntegrityError):
        add_conversation(session, customer, status=ConversationStatus.HUMAN_TAKEOVER)


def test_stale_conversation_update_is_rejected(session, conversation):
    assert conversation.revision == 1
    session.execute(
        text("UPDATE conversation SET revision = revision + 1 WHERE id = :id"), {"id": conversation.id}
    )

    conversation.status = ConversationStatus.HUMAN_TAKEOVER
    with pytest.raises(StaleDataError):
        session.flush()


def test_message_cannot_use_another_business_conversation(session, conversation):
    with pytest.raises(IntegrityError):
        add_message(session, conversation, business_id=add_business(session).id)


def test_provider_message_is_stored_once(session, conversation):
    add_message(session, conversation, provider_message_id="SM0001")

    with pytest.raises(IntegrityError):
        add_message(session, conversation, provider_message_id="SM0001")


def test_same_text_with_new_provider_ids_is_kept(session, conversation):
    for provider_message_id in ("SM0001", "SM0002"):
        add_message(session, conversation, provider_message_id=provider_message_id, body="saturday?")

    stored = session.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id)
    )
    assert stored == 2


@pytest.mark.parametrize(
    ("direction", "status"),
    [(MessageDirection.INBOUND, MessageStatus.QUEUED), (MessageDirection.OUTBOUND, MessageStatus.RECEIVED)],
)
def test_status_must_match_direction(session, conversation, direction, status):
    with pytest.raises(IntegrityError):
        add_message(session, conversation, direction=direction, status=status)


def test_queued_replies_keep_insertion_order(session, conversation):
    queued = {"direction": MessageDirection.OUTBOUND, "status": MessageStatus.QUEUED}
    first = add_message(session, conversation, **queued)
    second = add_message(session, conversation, **queued)

    assert first.provider_message_id is None
    assert first.seq < second.seq


def test_body_over_twilio_limit_is_rejected(session, conversation):
    with pytest.raises(DataError):
        add_message(session, conversation, body="x" * 1601)
