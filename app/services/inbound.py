import uuid
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.keywords import Keyword
from app.models import (
    Business,
    Conversation,
    ConversationStatus,
    Customer,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.consent import apply_keyword

Phone = Annotated[str, Field(pattern=r"^\+[1-9][0-9]{1,14}$")]


class InboundText(BaseModel):
    # what both Twilio and the browser simulator turn a received text into
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["twilio", "simulator"]
    account: Annotated[str, Field(min_length=1, max_length=64)]
    message_id: Annotated[str, Field(min_length=1, max_length=64)]
    sender: Phone
    recipient: Phone
    # Twilio's limit; a longer text is refused, never cut short into something that looks complete
    body: Annotated[str, Field(max_length=1600)]


@dataclass(frozen=True)
class Received:
    message: Message
    # a redelivery of a text already stored and handled
    duplicate: bool
    keyword: Keyword | None = None


def receive_text(session: Session, inbound: InboundText) -> Received:
    # the business comes from the number that was texted, never from anything the sender says
    business_id = session.scalar(select(Business.id).where(Business.texting_number == inbound.recipient))
    if business_id is None:
        raise LookupError(f"no business texts from {inbound.recipient}")
    if session.scalar(select(Business.id).where(Business.texting_number == inbound.sender)):
        # two automated numbers replying to each other would never stop
        raise ValueError(f"{inbound.sender} is a business number, not a customer")
    if stored := _stored(session, inbound):
        return Received(stored, duplicate=True)

    conversation_id = _open_conversation(session, business_id, _customer(session, business_id, inbound.sender))
    # concurrent deliveries of one text: the database keeps the first and this returns nothing
    message_id = session.scalar(
        insert(Message)
        .values(
            business_id=business_id,
            conversation_id=conversation_id,
            direction=MessageDirection.INBOUND,
            status=MessageStatus.RECEIVED,
            provider=inbound.provider,
            provider_account=inbound.account,
            provider_message_id=inbound.message_id,
            body=inbound.body,
        )
        .on_conflict_do_nothing(index_elements=["provider", "provider_account", "provider_message_id"])
        .returning(Message.id)
    )
    if message_id is None:
        return Received(_stored(session, inbound), duplicate=True)
    message = session.get_one(Message, message_id)
    return Received(message, duplicate=False, keyword=apply_keyword(session, message))


def _stored(session: Session, inbound: InboundText) -> Message | None:
    return session.scalar(
        select(Message).where(
            Message.provider == inbound.provider,
            Message.provider_account == inbound.account,
            Message.provider_message_id == inbound.message_id,
        )
    )


def _customer(session: Session, business_id: uuid.UUID, phone: str) -> uuid.UUID:
    # two first texts at once would otherwise create the customer twice
    session.execute(
        insert(Customer)
        .values(business_id=business_id, phone=phone)
        .on_conflict_do_nothing(index_elements=["business_id", "phone"])
    )
    return session.scalars(
        select(Customer.id).where(Customer.business_id == business_id, Customer.phone == phone)
    ).one()


def _open_conversation(session: Session, business_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID:
    session.execute(
        insert(Conversation)
        .values(business_id=business_id, customer_id=customer_id, status=ConversationStatus.ACTIVE, revision=1)
        .on_conflict_do_nothing(
            index_elements=["business_id", "customer_id"], index_where=text("status <> 'ARCHIVED'")
        )
    )
    # every new text bumps the revision, so work based on an older view of the chat is stale
    return session.scalars(
        update(Conversation)
        .where(
            Conversation.business_id == business_id,
            Conversation.customer_id == customer_id,
            Conversation.status != ConversationStatus.ARCHIVED,
        )
        .values(last_activity_at=func.now(), revision=Conversation.revision + 1)
        .returning(Conversation.id)
    ).one()
