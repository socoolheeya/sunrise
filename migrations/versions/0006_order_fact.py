"""add order_fact read model

Revision ID: 0006_order_fact
Revises: 0005_datatalk_snapshots
Create Date: 2026-06-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_order_fact"
down_revision = "0005_datatalk_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_facts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=False),
        sa.Column("visitor_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("channel", sa.String(length=128), nullable=False, server_default="unknown"),
        sa.Column("onsite_matched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attributed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attributed_channel", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "order_id", name="uq_order_facts_tenant_order"),
    )
    op.create_index(
        "ix_order_facts_tenant_time",
        "order_facts",
        ["tenant_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_order_facts_tenant_time", table_name="order_facts")
    op.drop_table("order_facts")
