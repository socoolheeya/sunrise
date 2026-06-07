"""영속성 스키마 (SQLAlchemy ORM).

수집(write)과 분석(read)이 공유하는 단일 events 테이블.
Clean Architecture 상 이는 인프라 세부사항이며, 도메인 계층은 이 모듈을 모른다.
어댑터(Repository 구현)만 참조한다.
"""

from __future__ import annotations

import decimal
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        # 멱등성: 같은 테넌트 내 동일 event_id 는 1건만 저장.
        UniqueConstraint("tenant_id", "event_id", name="uq_events_tenant_event"),
        Index("ix_events_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_events_tenant_visitor", "tenant_id", "visitor_id"),
        Index("ix_events_tenant_session", "tenant_id", "session_id"),
        Index("ix_events_tenant_order", "tenant_id", "order_id"),
    )

    # SQLite 는 INTEGER PRIMARY KEY 만 자동증가하므로 dialect variant 로 처리.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(128))
    session_id: Mapped[str | None] = mapped_column(String(128))
    order_id: Mapped[str | None] = mapped_column(String(128))
    utm_source: Mapped[str | None] = mapped_column(String(128))
    utm_medium: Mapped[str | None] = mapped_column(String(128))
    utm_campaign: Mapped[str | None] = mapped_column(String(128))
    landing_page: Mapped[str | None] = mapped_column(String(2048))
    amount: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductFeatureRow(Base):
    __tablename__ = "product_features"
    __table_args__ = (
        UniqueConstraint("tenant_id", "product_id", name="uq_product_features_tenant_product"),
        Index("ix_product_features_tenant_category", "tenant_id", "category"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128))
    price: Mapped[float | None] = mapped_column(Float)
    original_price: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    return_rate: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AudienceMaterializationRow(Base):
    __tablename__ = "audience_materializations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "audience_id", name="uq_audience_materialization"),
        Index("ix_audience_materializations_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    audience_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_json: Mapped[str] = mapped_column(Text, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_visitor_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataTalkSnapshotRow(Base):
    __tablename__ = "datatalk_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "snapshot_id", name="uq_datatalk_snapshot"),
        Index("ix_datatalk_snapshots_tenant_period", "tenant_id", "start_at", "end_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
