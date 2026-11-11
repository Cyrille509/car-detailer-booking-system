"""check business name is not blank

Revision ID: 5d9f2b6a1c83
Revises: c41d0a9e7b25
Create Date: 2026-11-10 20:30:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "5d9f2b6a1c83"
down_revision: str | Sequence[str] | None = "c41d0a9e7b25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(op.f("ck_business_name_not_blank"), "business", r"name ~ '\S'")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_business_name_not_blank"), "business", type_="check")
