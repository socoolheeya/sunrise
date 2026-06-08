"""add cohort_retention read model

Revision ID: 0008_cohort_retention
Revises: 0007_customer_segment_daily
Create Date: 2026-06-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_cohort_retention"
down_revision = "0007_customer_segment_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cohort_retention",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("cohort_type", sa.String(length=32), nullable=False),
        sa.Column("granularity", sa.String(length=16), nullable=False),
        sa.Column("cohort", sa.String(length=32), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("base_count", sa.Integer(), nullable=False),
        sa.Column("retained_count", sa.Integer(), nullable=False),
        sa.Column("retention_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "cohort_type", "granularity", "cohort", "offset", name="uq_cohort_retention_cell"),
    )
    op.create_index(
        "ix_cohort_retention_lookup",
        "cohort_retention",
        ["tenant_id", "cohort_type", "granularity"],
    )


def downgrade() -> None:
    op.drop_index("ix_cohort_retention_lookup", table_name="cohort_retention")
    op.drop_table("cohort_retention")
