"""add customer, conversation and message tables

Revision ID: 8b2e5d71c0a4
Revises: 3f8a1c2d9e47
Create Date: 2026-11-10 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b2e5d71c0a4"
down_revision: str | Sequence[str] | None = "3f8a1c2d9e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(r"phone ~ '^\+[1-9][0-9]{1,14}$'", name=op.f("ck_customer_phone_e164")),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], name=op.f("fk_customer_business_id_business")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer")),
        sa.UniqueConstraint("business_id", "id", name=op.f("uq_customer_business_id_id")),
        sa.UniqueConstraint("business_id", "phone", name=op.f("uq_customer_business_id_phone")),
    )
    op.create_table(
        "conversation",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'HUMAN_TAKEOVER', 'ARCHIVED')", name=op.f("ck_conversation_status")
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customer.business_id", "customer.id"],
            name=op.f("fk_conversation_business_id_customer"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation")),
        sa.UniqueConstraint("business_id", "id", name=op.f("uq_conversation_business_id_id")),
    )
    op.create_index(
        "uq_conversation_open",
        "conversation",
        ["business_id", "customer_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'ARCHIVED'"),
    )
    op.create_table(
        "message",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_account", sa.String(length=64), nullable=False),
        sa.Column("provider_message_id", sa.String(length=64), nullable=True),
        sa.Column("body", sa.String(length=1600), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("direction IN ('INBOUND', 'OUTBOUND')", name=op.f("ck_message_direction")),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'QUEUED', 'SENT', 'DELIVERED', 'FAILED')", name=op.f("ck_message_status")
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "conversation_id"],
            ["conversation.business_id", "conversation.id"],
            name=op.f("fk_message_business_id_conversation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message")),
        sa.UniqueConstraint(
            "provider",
            "provider_account",
            "provider_message_id",
            name=op.f("uq_message_provider_provider_account_provider_message_id"),
        ),
    )
    op.create_index("ix_message_conversation_id_seq", "message", ["conversation_id", "seq"])


def downgrade() -> None:
    op.drop_table("message")
    op.drop_table("conversation")
    op.drop_table("customer")
