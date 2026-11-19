"""add sms consent table

Revision ID: a91d4e7c3b58
Revises: f2c6a8d41e73
Create Date: 2026-11-19 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91d4e7c3b58"
down_revision: str | Sequence[str] | None = "f2c6a8d41e73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_message_seq"), "message", ["seq"])
    op.create_table(
        "sms_consent",
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message_seq", sa.BigInteger(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('OPTED_IN', 'OPTED_OUT')", name=op.f("ck_sms_consent_status")),
        sa.ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customer.business_id", "customer.id"],
            name=op.f("fk_sms_consent_business_id_customer"),
        ),
        sa.ForeignKeyConstraint(["message_seq"], ["message.seq"], name=op.f("fk_sms_consent_message_seq_message")),
        sa.PrimaryKeyConstraint("customer_id", name=op.f("pk_sms_consent")),
    )


def downgrade() -> None:
    op.drop_table("sms_consent")
    op.drop_constraint(op.f("uq_message_seq"), "message", type_="unique")
