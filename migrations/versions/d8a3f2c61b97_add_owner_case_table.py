"""add owner case table

Revision ID: d8a3f2c61b97
Revises: c5e2b7f90d14
Create Date: 2026-11-19 20:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8a3f2c61b97"
down_revision: str | Sequence[str] | None = "c5e2b7f90d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owner_case",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("next_action", sa.String(length=200), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("reason IN ('CUSTOMER_ASKED')", name=op.f("ck_owner_case_reason")),
        sa.CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'AWAITING_CUSTOMER', 'RESOLVED')", name=op.f("ck_owner_case_status")
        ),
        sa.CheckConstraint(r"next_action ~ '\S'", name=op.f("ck_owner_case_next_action_not_blank")),
        sa.CheckConstraint(
            "(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name=op.f("ck_owner_case_resolved_at_matches_status")
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "conversation_id"],
            ["conversation.business_id", "conversation.id"],
            name=op.f("fk_owner_case_business_id_conversation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owner_case")),
    )
    op.create_index(
        "uq_owner_case_unresolved",
        "owner_case",
        ["conversation_id", "reason"],
        unique=True,
        postgresql_where=sa.text("status <> 'RESOLVED'"),
    )


def downgrade() -> None:
    op.drop_table("owner_case")
