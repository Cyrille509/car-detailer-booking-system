"""check message status matches direction

Revision ID: c41d0a9e7b25
Revises: 8b2e5d71c0a4
Create Date: 2026-11-10 19:53:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "c41d0a9e7b25"
down_revision: str | Sequence[str] | None = "8b2e5d71c0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_message_direction_status"), "message", "(direction = 'INBOUND') = (status = 'RECEIVED')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_message_direction_status"), "message", type_="check")
