"""add datatalk snapshots

Revision ID: 0005_datatalk_snapshots
Revises: 0004_audience_materializations
Create Date: 2026-06-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_datatalk_snapshots"
down_revision = "0004_audience_materializations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datatalk_snapshots",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "snapshot_id", name="uq_datatalk_snapshot"),
    )
    op.create_index(
        "ix_datatalk_snapshots_tenant_period",
        "datatalk_snapshots",
        ["tenant_id", "start_at", "end_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_datatalk_snapshots_tenant_period", table_name="datatalk_snapshots")
    op.drop_table("datatalk_snapshots")
