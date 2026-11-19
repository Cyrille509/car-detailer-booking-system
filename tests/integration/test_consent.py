import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.keywords import Keyword
from app.models import (
    Business,
    ConsentStatus,
    Conversation,
    Customer,
    Message,
    MessageDirection,
    MessageStatus,
    SmsConsent,
)
from app.services.consent import apply_keyword, can_text

PHONE = "+12015550123"


def add(session, row):
    session.add(row)
    session.flush()
    return row


def add_conversation(session, phone=PHONE) -> Conversation:
    business = add(session, Business(name="Shine Mobile Detailing", timezone="America/New_York"))
    customer = add(session, Customer(business_id=business.id, phone=phone))
    return add(session, Conversation(business_id=business.id, customer_id=customer.id))


def text(session, conversation, body, direction=MessageDirection.INBOUND) -> Message:
    status = MessageStatus.RECEIVED if direction == MessageDirection.INBOUND else MessageStatus.QUEUED
    return add(
        session,
        Message(
            business_id=conversation.business_id,
            conversation_id=conversation.id,
            direction=direction,
            status=status,
            provider="simulator",
            provider_account="local",
            body=body,
        ),
    )


@pytest.fixture
def conversation(session) -> Conversation:
    return add_conversation(session)


def test_customer_who_texted_first_can_get_replies(session, conversation):
    assert can_text(session, conversation.customer_id)


def test_stop_and_start(session, conversation):
    assert apply_keyword(session, text(session, conversation, "Stop.")) == Keyword.OPT_OUT
    assert not can_text(session, conversation.customer_id)
    opted_out_at = session.scalar(select(SmsConsent.changed_at))

    assert apply_keyword(session, text(session, conversation, "START")) == Keyword.OPT_IN
    assert can_text(session, conversation.customer_id)
    assert session.scalar(select(SmsConsent.changed_at)) > opted_out_at


def test_text_handled_late_does_not_undo_a_later_one(session, conversation):
    stop = text(session, conversation, "STOP")
    start = text(session, conversation, "START")

    apply_keyword(session, start)
    apply_keyword(session, stop)

    assert can_text(session, conversation.customer_id)
    assert session.scalar(select(SmsConsent.message_seq)) == start.seq


def test_same_text_handled_twice_changes_nothing_more(session, conversation):
    stop = text(session, conversation, "STOP")
    apply_keyword(session, stop)
    changed_at = session.scalar(select(SmsConsent.changed_at))

    apply_keyword(session, stop)

    assert session.scalar(select(SmsConsent.changed_at)) == changed_at


@pytest.mark.parametrize(
    ("body", "keyword"),
    [("HELP", Keyword.HELP), ("cancel my appointment", None), ("suv full detail", None)],
    ids=["help", "booking request", "ordinary text"],
)
def test_other_texts_leave_consent_alone(session, conversation, body, keyword):
    assert apply_keyword(session, text(session, conversation, body)) == keyword
    assert session.scalar(select(SmsConsent)) is None


def test_outgoing_texts_never_change_consent(session, conversation):
    with pytest.raises(ValueError, match="customer's own texts"):
        apply_keyword(session, text(session, conversation, "STOP", MessageDirection.OUTBOUND))


def test_opting_out_of_one_business_leaves_the_other(session, conversation):
    # the same person texting two detailers is two customers
    other = add_conversation(session)

    apply_keyword(session, text(session, conversation, "STOP"))

    assert not can_text(session, conversation.customer_id)
    assert can_text(session, other.customer_id)


def test_stop_lands_on_the_sender_only(session, conversation):
    neighbor = add(session, Customer(business_id=conversation.business_id, phone="+12015550199"))

    apply_keyword(session, text(session, conversation, "STOP"))

    assert can_text(session, neighbor.id)
    assert session.scalar(select(SmsConsent.customer_id)) == conversation.customer_id


def test_consent_cannot_point_at_another_business_customer(session, conversation):
    other = add_conversation(session)
    message = text(session, conversation, "STOP")

    with pytest.raises(IntegrityError) as caught:
        add(
            session,
            SmsConsent(
                customer_id=conversation.customer_id,
                business_id=other.business_id,
                status=ConsentStatus.OPTED_OUT,
                message_seq=message.seq,
            ),
        )
    assert caught.value.orig.diag.constraint_name == "fk_sms_consent_business_id_customer"
