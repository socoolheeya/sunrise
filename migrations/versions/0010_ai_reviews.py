"""add ai review queue

Revision ID: 0010_ai_reviews
Revises: 0009_ingestion_outbox
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_ai_reviews"
down_revision = "0009_ingestion_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_reviews",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("guardrail_json", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_reviews_tenant_status", "ai_reviews", ["tenant_id", "status", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_reviews_tenant_status", table_name="ai_reviews")
    op.drop_table("ai_reviews")
