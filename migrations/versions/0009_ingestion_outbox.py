"""add ingestion outbox

Revision ID: 0009_ingestion_outbox
Revises: 0008_cohort_retention
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_ingestion_outbox"
down_revision = "0008_cohort_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_outbox",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_ingestion_outbox_event"),
    )
    op.create_index(
        "ix_ingestion_outbox_tenant_id", "ingestion_outbox", ["tenant_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_outbox_tenant_id", table_name="ingestion_outbox")
    op.drop_table("ingestion_outbox")
