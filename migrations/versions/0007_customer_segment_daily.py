"""add customer_segment_daily read model

Revision ID: 0007_customer_segment_daily
Revises: 0006_order_fact
Create Date: 2026-06-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_customer_segment_daily"
down_revision = "0006_order_fact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_segment_daily",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visit_segment", sa.String(length=32), nullable=False),
        sa.Column("purchase_segment", sa.String(length=32), nullable=False),
        sa.Column("revenue", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "customer_id", "as_of", name="uq_customer_segment_daily"),
    )
    op.create_index(
        "ix_customer_segment_daily_tenant_asof",
        "customer_segment_daily",
        ["tenant_id", "as_of"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_segment_daily_tenant_asof", table_name="customer_segment_daily")
    op.drop_table("customer_segment_daily")
