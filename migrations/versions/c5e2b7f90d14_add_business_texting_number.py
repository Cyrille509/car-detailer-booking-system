"""add business texting number

Revision ID: c5e2b7f90d14
Revises: a91d4e7c3b58
Create Date: 2026-11-19 19:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e2b7f90d14"
down_revision: str | Sequence[str] | None = "a91d4e7c3b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business", sa.Column("texting_number", sa.String(length=16), nullable=True))
    op.create_unique_constraint(op.f("uq_business_texting_number"), "business", ["texting_number"])
    op.create_check_constraint(
        op.f("ck_business_texting_number_e164"), "business", r"texting_number ~ '^\+[1-9][0-9]{1,14}$'"
    )


def downgrade() -> None:
    op.drop_column("business", "texting_number")
