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


class OrderFactRow(Base):
    """주문 단위 사실 read model (order_id 중복제거된 주문 원장)."""

    __tablename__ = "order_facts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", name="uq_order_facts_tenant_order"),
        Index("ix_order_facts_tenant_time", "tenant_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    channel: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    onsite_matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attributed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attributed_channel: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelArtifactRow(Base):
    """DB 기반 모델 레지스트리: 버전·상태(staging/production/archived) 관리.

    서빙은 테넌트의 production 버전을 우선 사용하고, 없으면 패키지 동봉 artifact
    (global default seed)로 폴백한다. promote/rollback 으로 코드 배포 없이 무중단 교체.
    """

    __tablename__ = "model_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_name", "version", name="uq_model_artifacts_version"
        ),
        Index("ix_model_artifacts_active", "tenant_id", "model_name", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="staging")
    artifact_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiReviewRow(Base):
    """AI 생성물 사람 검토 큐 (review workflow).

    가드레일이 human review 를 요구한 생성물을 pending 으로 적재하고,
    승인/반려 상태 전이를 기록한다.
    """

    __tablename__ = "ai_reviews"
    __table_args__ = (
        Index("ix_ai_reviews_tenant_status", "tenant_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    guardrail_json: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionOutboxRow(Base):
    """수집 outbox: Kafka 발행 실패 시 이벤트를 보존했다가 복구 후 재발행한다.

    Kafka(+DLQ) 전면 장애에도 at-least-once 를 보장하는 내구성 backstop.
    (tenant_id, event_id) 유니크로 중복 적재를 방지한다.
    """

    __tablename__ = "ingestion_outbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_ingestion_outbox_event"),
        Index("ix_ingestion_outbox_tenant_id", "tenant_id", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerSegmentDailyRow(Base):
    """방문/구매 lifecycle 세그먼트의 일자별 스냅샷 read model.

    같은 (tenant, customer, as_of) 는 1건. 두 as_of 스냅샷을 비교해 세그먼트
    이동(transition) 을 산출한다.
    """

    __tablename__ = "customer_segment_daily"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "as_of", name="uq_customer_segment_daily"
        ),
        Index("ix_customer_segment_daily_tenant_asof", "tenant_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visit_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    purchase_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CohortRetentionRow(Base):
    """일/주/월 코호트 retention read model (셀 단위 한 행)."""

    __tablename__ = "cohort_retention"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "cohort_type", "granularity", "cohort", "offset",
            name="uq_cohort_retention_cell",
        ),
        Index(
            "ix_cohort_retention_lookup",
            "tenant_id", "cohort_type", "granularity",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cohort_type: Mapped[str] = mapped_column(String(32), nullable=False)
    granularity: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort: Mapped[str] = mapped_column(String(32), nullable=False)
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    base_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retained_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
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
