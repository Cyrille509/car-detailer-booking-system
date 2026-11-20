import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.domain.keywords import Keyword
from app.models import Business, CaseReason, CaseStatus, Conversation, ConversationStatus, OwnerCase
from app.services.consent import can_text
from app.services.handoff import resume_automation
from app.services.inbound import InboundText, receive_text

BUSINESS_NUMBER = "+12015550100"


def add(session, row):
    session.add(row)
    session.flush()
    return row


def add_business(session, number=BUSINESS_NUMBER) -> Business:
    return add(session, Business(name="Shine Mobile Detailing", timezone="America/New_York", texting_number=number))


def send(session, body, recipient=BUSINESS_NUMBER):
    text = InboundText(
        provider="simulator",
        account="local",
        message_id=uuid.uuid4().hex,
        sender="+12015550123",
        recipient=recipient,
        body=body,
    )
    return receive_text(session, text)


def conversation_of(session, received) -> Conversation:
    return session.get_one(Conversation, received.message.conversation_id, populate_existing=True)


def cases(session) -> list[OwnerCase]:
    return list(session.scalars(select(OwnerCase).order_by(OwnerCase.opened_at, OwnerCase.status)))


@pytest.fixture
def business(session) -> Business:
    return add_business(session)


def test_owner_pauses_automation_and_opens_a_case(session, business):
    send(session, "suv full detail")
    received = send(session, "OWNER")
    conversation = conversation_of(session, received)

    assert received.keyword == Keyword.OWNER
    assert conversation.status == ConversationStatus.HUMAN_TAKEOVER
    [case] = cases(session)
    assert received.case is case
    assert (case.business_id, case.conversation_id) == (business.id, conversation.id)
    assert (case.reason, case.status, case.resolved_at) == (CaseReason.CUSTOMER_ASKED, CaseStatus.OPEN, None)
    assert case.next_action == "Reply to the customer, then resume automation"


def test_asking_twice_keeps_one_case(session, business):
    first = send(session, "OWNER")
    second = send(session, "owner!")

    assert second.case is first.case
    assert len(cases(session)) == 1
    assert conversation_of(session, first).status == ConversationStatus.HUMAN_TAKEOVER


@pytest.mark.parametrize("body", ["STOP", "HELP", "can the owner call me?"])
def test_other_texts_do_not_open_a_case(session, business, body):
    send(session, body)

    assert cases(session) == []


def test_customer_who_opted_out_can_still_reach_the_owner(session, business):
    send(session, "STOP")
    received = send(session, "OWNER")

    assert len(cases(session)) == 1
    assert not can_text(session, conversation_of(session, received).customer_id)


def test_owner_resumes_from_what_they_saw(session, business):
    received = send(session, "OWNER")
    [case] = cases(session)
    seen = conversation_of(session, received).revision

    resume_automation(session, case, seen)

    assert conversation_of(session, received).status == ConversationStatus.ACTIVE
    assert case.status == CaseStatus.RESOLVED
    assert isinstance(case.resolved_at, datetime)


def test_resuming_after_a_new_text_needs_another_look(session, business):
    received = send(session, "OWNER")
    [case] = cases(session)
    seen = conversation_of(session, received).revision
    send(session, "actually it's a sedan")

    with pytest.raises(StaleDataError, match=f"since revision {seen}"):
        resume_automation(session, case, seen)
    assert conversation_of(session, received).status == ConversationStatus.HUMAN_TAKEOVER


def test_resolved_case_cannot_be_resumed_again(session, business):
    received = send(session, "OWNER")
    [case] = cases(session)
    resume_automation(session, case, conversation_of(session, received).revision)

    with pytest.raises(ValueError, match="already resolved"):
        resume_automation(session, case, conversation_of(session, received).revision)


def test_case_resumed_from_another_tab_is_not_resumed_twice(session, business):
    received = send(session, "OWNER")
    [case] = cases(session)
    # this tab still holds the conversation it loaded
    shown = conversation_of(session, received)
    # the other tab's resume, which this tab's copies haven't seen
    session.execute(
        update(Conversation).where(Conversation.id == case.conversation_id).values(status=ConversationStatus.ACTIVE),
        execution_options={"synchronize_session": False},
    )

    with pytest.raises(ValueError, match="not with the owner"):
        resume_automation(session, case, shown.revision)


def test_asking_again_after_resuming_opens_a_new_case(session, business):
    received = send(session, "OWNER")
    [first] = cases(session)
    resume_automation(session, first, conversation_of(session, received).revision)

    again = send(session, "OWNER")

    assert (again.case.id != first.id, again.case.status) == (True, CaseStatus.OPEN)
    assert [case.status for case in cases(session)] == [CaseStatus.OPEN, CaseStatus.RESOLVED]
    assert session.scalar(select(func.count()).select_from(OwnerCase)) == 2


def unresolved(conversation, **changes) -> OwnerCase:
    fields = {
        "business_id": conversation.business_id,
        "conversation_id": conversation.id,
        "reason": CaseReason.CUSTOMER_ASKED,
        "status": CaseStatus.OPEN,
        "next_action": "Reply to the customer",
    }
    return OwnerCase(**(fields | changes))


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        pytest.param({}, "uq_owner_case_unresolved", id="second unresolved case"),
        pytest.param({"next_action": "  "}, "ck_owner_case_next_action_not_blank", id="no next action"),
        pytest.param(
            {"status": CaseStatus.RESOLVED}, "ck_owner_case_resolved_at_matches_status", id="resolved without a time"
        ),
        pytest.param(
            {"status": CaseStatus.IN_PROGRESS, "resolved_at": datetime(2026, 11, 19)},
            "ck_owner_case_resolved_at_matches_status",
            id="time on an unresolved case",
        ),
    ],
)
def test_database_refuses_inconsistent_cases(session, business, changes, constraint):
    conversation = conversation_of(session, send(session, "OWNER"))

    with pytest.raises(IntegrityError) as caught:
        add(session, unresolved(conversation, **changes))
    assert caught.value.orig.diag.constraint_name == constraint


def test_case_cannot_point_at_another_business_conversation(session, business):
    conversation = conversation_of(session, send(session, "hi"))
    other = add_business(session, number="+12015550200")

    with pytest.raises(IntegrityError) as caught:
        add(session, unresolved(conversation, business_id=other.id))
    assert caught.value.orig.diag.constraint_name == "fk_owner_case_business_id_conversation"
