import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.keywords import Keyword, find_keyword
from app.models import ConsentStatus, Conversation, Message, MessageDirection, SmsConsent

STATUS_FOR = {Keyword.OPT_OUT: ConsentStatus.OPTED_OUT, Keyword.OPT_IN: ConsentStatus.OPTED_IN}


def apply_keyword(session: Session, message: Message) -> Keyword | None:
    if message.direction != MessageDirection.INBOUND:
        raise ValueError("only the customer's own texts change consent")
    keyword = find_keyword(message.body)
    if keyword not in STATUS_FOR:
        return keyword

    # the customer comes from the message itself, so one person's STOP can't land on another
    customer_id = session.scalar(select(Conversation.customer_id).where(Conversation.id == message.conversation_id))
    change = insert(SmsConsent).values(
        customer_id=customer_id,
        business_id=message.business_id,
        status=STATUS_FOR[keyword],
        message_seq=message.seq,
        # the actual moment, not the transaction start that now() returns
        changed_at=func.clock_timestamp(),
    )
    session.execute(
        change.on_conflict_do_update(
            index_elements=[SmsConsent.customer_id],
            set_={
                "status": change.excluded.status,
                "message_seq": change.excluded.message_seq,
                "changed_at": change.excluded.changed_at,
            },
            # texts can be handled out of order; the one that arrived last decides
            where=SmsConsent.message_seq < change.excluded.message_seq,
        )
    )
    return keyword


def can_text(session: Session, customer_id: uuid.UUID) -> bool:
    status = session.scalar(select(SmsConsent.status).where(SmsConsent.customer_id == customer_id))
    return status != ConsentStatus.OPTED_OUT
