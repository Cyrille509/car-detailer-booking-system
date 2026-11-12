"""add job request table

Revision ID: b3d8f61a2c94
Revises: e7a4c2b19d56
Create Date: 2026-11-11 14:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d8f61a2c94"
down_revision: str | Sequence[str] | None = "e7a4c2b19d56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_request",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("vehicle_class", sa.String(length=40), nullable=True),
        sa.Column("package_id", sa.String(length=40), nullable=True),
        sa.Column(
            "add_on_ids", postgresql.ARRAY(sa.String(length=40)), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            "conditions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("unresolved_scope", sa.String(length=1600), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("country ~ '^[A-Z]{2}$'", name=op.f("ck_job_request_country_code")),
        sa.CheckConstraint(
            "array_position(add_on_ids, NULL) IS NULL", name=op.f("ck_job_request_add_on_ids_not_null")
        ),
        sa.CheckConstraint("jsonb_typeof(conditions) = 'object'", name=op.f("ck_job_request_conditions_object")),
        sa.ForeignKeyConstraint(
            ["business_id", "conversation_id"],
            ["conversation.business_id", "conversation.id"],
            name=op.f("fk_job_request_business_id_conversation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_request")),
    )
    op.create_index(op.f("ix_job_request_conversation_id"), "job_request", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("job_request")
