"""add estimate table

Revision ID: f2c6a8d41e73
Revises: b3d8f61a2c94
Create Date: 2026-11-11 17:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2c6a8d41e73"
down_revision: str | Sequence[str] | None = "b3d8f61a2c94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_catalog_version_business_id_id"), "catalog_version", ["business_id", "id"])
    op.create_unique_constraint(op.f("uq_job_request_business_id_id"), "job_request", ["business_id", "id"])
    op.create_table(
        "estimate",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("job_request_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("intake", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal_minor", sa.BigInteger(), nullable=False),
        sa.Column("tax_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("deposit_minor", sa.BigInteger(), nullable=False),
        sa.Column("event_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_minutes", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'NEEDS_REVIEW', 'ISSUED', 'ACCEPTED', 'SUPERSEDED', 'EXPIRED', 'DECLINED')",
            name=op.f("ck_estimate_status"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_estimate_version_positive")),
        sa.CheckConstraint("subtotal_minor >= 0 AND tax_minor >= 0", name=op.f("ck_estimate_amounts_not_negative")),
        sa.CheckConstraint("total_minor = subtotal_minor + tax_minor", name=op.f("ck_estimate_total_adds_up")),
        sa.CheckConstraint(
            "deposit_minor > 0 AND deposit_minor <= total_minor", name=op.f("ck_estimate_deposit_within_total")
        ),
        sa.CheckConstraint("expires_at > issued_at", name=op.f("ck_estimate_expires_after_issue")),
        sa.ForeignKeyConstraint(
            ["business_id", "job_request_id"],
            ["job_request.business_id", "job_request.id"],
            name=op.f("fk_estimate_business_id_job_request"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "catalog_version_id"],
            ["catalog_version.business_id", "catalog_version.id"],
            name=op.f("fk_estimate_business_id_catalog_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimate")),
        sa.UniqueConstraint("job_request_id", "version", name=op.f("uq_estimate_job_request_id_version")),
    )
    op.create_index(
        "uq_estimate_open",
        "estimate",
        ["job_request_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('DRAFT', 'NEEDS_REVIEW', 'ISSUED', 'ACCEPTED')"),
    )
    op.execute(
        """
        CREATE FUNCTION keep_estimate_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'estimate snapshots cannot be changed',
                    ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER estimate_keep_snapshot BEFORE UPDATE ON estimate "
        "FOR EACH ROW EXECUTE FUNCTION keep_estimate_snapshot()"
    )


def downgrade() -> None:
    # dropping the table drops its trigger, which frees the function
    op.drop_table("estimate")
    op.execute("DROP FUNCTION keep_estimate_snapshot()")
    op.drop_constraint(op.f("uq_job_request_business_id_id"), "job_request", type_="unique")
    op.drop_constraint(op.f("uq_catalog_version_business_id_id"), "catalog_version", type_="unique")
