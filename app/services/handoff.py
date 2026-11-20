from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import CaseReason, CaseStatus, Conversation, ConversationStatus, Message, OwnerCase


def hand_to_owner(session: Session, message: Message) -> OwnerCase:
    # automation pauses at once; only the owner can turn it back on
    session.execute(
        update(Conversation)
        .where(Conversation.id == message.conversation_id, Conversation.status == ConversationStatus.ACTIVE)
        .values(status=ConversationStatus.HUMAN_TAKEOVER, revision=Conversation.revision + 1)
    )
    # asking twice keeps the one case already waiting for the owner
    session.execute(
        insert(OwnerCase)
        .values(
            business_id=message.business_id,
            conversation_id=message.conversation_id,
            reason=CaseReason.CUSTOMER_ASKED,
            status=CaseStatus.OPEN,
            next_action="Reply to the customer, then resume automation",
        )
        .on_conflict_do_nothing(index_elements=["conversation_id", "reason"], index_where=text("status <> 'RESOLVED'"))
    )
    return session.scalars(
        select(OwnerCase).where(
            OwnerCase.conversation_id == message.conversation_id,
            OwnerCase.reason == CaseReason.CUSTOMER_ASKED,
            OwnerCase.status != CaseStatus.RESOLVED,
        )
    ).one()


def resume_automation(session: Session, case: OwnerCase, seen_revision: int) -> None:
    if case.status == CaseStatus.RESOLVED:
        raise ValueError("case is already resolved")
    conversation = session.get_one(Conversation, case.conversation_id, populate_existing=True)
    if conversation.status != ConversationStatus.HUMAN_TAKEOVER:
        raise ValueError(f"conversation is {conversation.status}, not with the owner")
    # the owner resumes from what they saw; a text that arrived since then needs a look first
    if conversation.revision != seen_revision:
        raise StaleDataError(f"conversation changed since revision {seen_revision}")

    conversation.status = ConversationStatus.ACTIVE
    case.status = CaseStatus.RESOLVED
    case.resolved_at = func.clock_timestamp()
    session.flush()
