"""add event attribution fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("session_id", sa.String(length=128), nullable=True))
    op.add_column("events", sa.Column("order_id", sa.String(length=128), nullable=True))
    op.add_column("events", sa.Column("utm_source", sa.String(length=128), nullable=True))
    op.add_column("events", sa.Column("utm_medium", sa.String(length=128), nullable=True))
    op.add_column("events", sa.Column("utm_campaign", sa.String(length=128), nullable=True))
    op.add_column("events", sa.Column("landing_page", sa.String(length=2048), nullable=True))
    op.create_index("ix_events_tenant_session", "events", ["tenant_id", "session_id"])
    op.create_index("ix_events_tenant_order", "events", ["tenant_id", "order_id"])


def downgrade() -> None:
    op.drop_index("ix_events_tenant_order", table_name="events")
    op.drop_index("ix_events_tenant_session", table_name="events")
    op.drop_column("events", "landing_page")
    op.drop_column("events", "utm_campaign")
    op.drop_column("events", "utm_medium")
    op.drop_column("events", "utm_source")
    op.drop_column("events", "order_id")
    op.drop_column("events", "session_id")
