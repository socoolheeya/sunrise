"""Add audience materialization snapshots.

Revision ID: 0004_audience_materializations
Revises: 0003_event_attribution_fields
Create Date: 2026-06-06 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_audience_materializations"
down_revision = "0003_event_attribution_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audience_materializations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("audience_id", sa.String(length=128), nullable=False),
        sa.Column("rule_hash", sa.String(length=64), nullable=False),
        sa.Column("rule_json", sa.Text(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("sample_visitor_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "audience_id",
            name="uq_audience_materialization",
        ),
    )
    op.create_index(
        "ix_audience_materializations_tenant_status",
        "audience_materializations",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audience_materializations_tenant_status",
        table_name="audience_materializations",
    )
    op.drop_table("audience_materializations")
