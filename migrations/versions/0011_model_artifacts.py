"""add model artifact registry

Revision ID: 0011_model_artifacts
Revises: 0010_ai_reviews
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_model_artifacts"
down_revision = "0010_ai_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="staging"),
        sa.Column("artifact_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "model_name", "version", name="uq_model_artifacts_version"),
    )
    op.create_index(
        "ix_model_artifacts_active", "model_artifacts", ["tenant_id", "model_name", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_model_artifacts_active", table_name="model_artifacts")
    op.drop_table("model_artifacts")
