"""add catalog version table

Revision ID: e7a4c2b19d56
Revises: 5d9f2b6a1c83
Create Date: 2026-11-11 11:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a4c2b19d56"
down_revision: str | Sequence[str] | None = "5d9f2b6a1c83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_version",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version > 0", name=op.f("ck_catalog_version_version_positive")),
        sa.ForeignKeyConstraint(
            ["business_id"], ["business.id"], name=op.f("fk_catalog_version_business_id_business")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_version")),
        sa.UniqueConstraint("business_id", "version", name=op.f("uq_catalog_version_business_id_version")),
    )
    # SQLSTATE 23000, so drivers report it as an integrity error
    op.execute(
        """
        CREATE FUNCTION reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = TG_TABLE_NAME || ' rows cannot be changed',
                ERRCODE = 'integrity_constraint_violation';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER catalog_version_reject_update BEFORE UPDATE ON catalog_version "
        "FOR EACH ROW EXECUTE FUNCTION reject_update()"
    )


def downgrade() -> None:
    # dropping the table drops its trigger, which frees the function
    op.drop_table("catalog_version")
    op.execute("DROP FUNCTION reject_update()")
