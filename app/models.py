import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates

UuidPk = Annotated[uuid.UUID, mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))]
Timestamp = Annotated[datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]


def enum_type(enum: type[StrEnum], name: str) -> Enum:
    return Enum(enum, name=name, native_enum=False, create_constraint=True, length=20)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"
    ARCHIVED = "ARCHIVED"


class MessageDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageStatus(StrEnum):
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class Business(Base):
    __tablename__ = "business"

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(200))
    # IANA name, e.g. America/New_York
    timezone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[Timestamp]

    @validates("timezone")
    def _known_timezone(self, _key: str, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {value!r}") from None
        return value


class Customer(Base):
    __tablename__ = "customer"
    __table_args__ = (
        UniqueConstraint("business_id", "phone"),
        # target for composite foreign keys, so child rows can't cross into another business
        UniqueConstraint("business_id", "id"),
        CheckConstraint(r"phone ~ '^\+[1-9][0-9]{1,14}$'", name="phone_e164"),
    )

    id: Mapped[UuidPk]
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    phone: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[Timestamp]


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "customer_id"], ["customer.business_id", "customer.id"]),
        UniqueConstraint("business_id", "id"),
        # one open thread per customer; archived threads stay as history
        Index(
            "uq_conversation_open",
            "business_id",
            "customer_id",
            unique=True,
            postgresql_where=text("status <> 'ARCHIVED'"),
        ),
    )

    id: Mapped[UuidPk]
    business_id: Mapped[uuid.UUID]
    customer_id: Mapped[uuid.UUID]
    status: Mapped[ConversationStatus] = mapped_column(
        enum_type(ConversationStatus, "status"), default=ConversationStatus.ACTIVE
    )
    # bumped on every update; a write based on an older revision raises StaleDataError
    revision: Mapped[int] = mapped_column()
    last_activity_at: Mapped[Timestamp]
    created_at: Mapped[Timestamp]

    __mapper_args__ = {"version_id_col": revision}


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "conversation_id"], ["conversation.business_id", "conversation.id"]
        ),
        UniqueConstraint("provider", "provider_account", "provider_message_id"),
        Index("ix_message_conversation_id_seq", "conversation_id", "seq"),
        # inbound texts are only ever RECEIVED; outbound ones never are
        CheckConstraint("(direction = 'INBOUND') = (status = 'RECEIVED')", name="direction_status"),
    )

    id: Mapped[UuidPk]
    # insertion order; now() is fixed per transaction, so created_at can tie
    seq: Mapped[int] = mapped_column(BigInteger, Identity())
    business_id: Mapped[uuid.UUID]
    conversation_id: Mapped[uuid.UUID]
    direction: Mapped[MessageDirection] = mapped_column(enum_type(MessageDirection, "direction"))
    status: Mapped[MessageStatus] = mapped_column(enum_type(MessageStatus, "status"))
    provider: Mapped[str] = mapped_column(String(16))
    provider_account: Mapped[str] = mapped_column(String(64))
    # null until the provider accepts an outbound send
    provider_message_id: Mapped[str | None] = mapped_column(String(64))
    # Twilio's documented body limit
    body: Mapped[str] = mapped_column(String(1600))
    created_at: Mapped[Timestamp]
