"""수집 HTTP 라우터 (Inbound Adapter).

Pydantic 입력을 도메인 명령(TrackingEvent)으로 변환해 유스케이스에 전달한다.
의존성 와이어링(컴포지션)은 FastAPI Depends 로 수행한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.rate_limit import get_collect_rate_limiter
from app.core.tenant import require_tenant
from app.events.registry import TRACKING_EVENT_SCHEMA_VERSION
from app.events.schemas import CollectRequest, CollectResponse
from app.ingestion.adapters.clickhouse import ClickHouseEventMirror
from app.ingestion.adapters.kafka import (
    KafkaEventRepository,
    KafkaPublishError,
    create_aiokafka_producer,
)
from app.ingestion.adapters.outbox import SqlOutboxStore
from app.ingestion.adapters.repository import SqlEventRepository
from app.ingestion.application.collect_events import CollectEvents
from app.ingestion.application.relay import RelayOutbox
from app.ingestion.domain.model import TrackingEvent

router = APIRouter(prefix="/v1", tags=["ingestion"])


def get_collect_use_case(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CollectEvents:
    if settings.ingestion_sink == "kafka":
        producer = getattr(request.app.state, "kafka_producer", None)
        if producer is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kafka producer is not ready",
            )
        outbox = (
            SqlOutboxStore(session) if settings.ingestion_outbox_enabled else None
        )
        return CollectEvents(
            KafkaEventRepository(
                producer,
                settings.kafka_raw_events_topic,
                dlq_topic=(
                    settings.kafka_dlq_topic if settings.kafka_dlq_enabled else None
                ),
                publish_attempts=settings.kafka_publish_attempts,
                outbox=outbox,
                outbox_max_pending=settings.ingestion_outbox_max_pending,
            )
        )
    mirror = None
    if settings.clickhouse_mirror_ingestion:
        client = getattr(request.app.state, "clickhouse_client", None)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ClickHouse client is not ready",
            )
        mirror = ClickHouseEventMirror(client, settings.clickhouse_events_table)
    return CollectEvents(SqlEventRepository(session, mirror=mirror))


async def configure_ingestion_sink(app, settings: Settings) -> None:
    """FastAPI lifespan hook: initialize Kafka producer only when configured."""
    if settings.ingestion_sink == "kafka":
        app.state.kafka_producer = await create_aiokafka_producer(settings)


async def close_ingestion_sink(app) -> None:
    producer = getattr(app.state, "kafka_producer", None)
    if producer is not None:
        await producer.stop()
        app.state.kafka_producer = None


@router.post(
    "/collect",
    response_model=CollectResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def collect(
    request: Request,
    payload: CollectRequest,
    tenant_id: str = Depends(require_tenant),
    settings: Settings = Depends(get_settings),
    use_case: CollectEvents = Depends(get_collect_use_case),
) -> CollectResponse:
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and int(content_length) > settings.max_collect_payload_bytes
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"max collect payload is {settings.max_collect_payload_bytes} bytes",
        )
    if not get_collect_rate_limiter().allow(
        tenant_id, limit=settings.collect_rate_limit_per_minute
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="collect rate limit exceeded",
        )
    if len(payload.events) > settings.max_events_per_request:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"max {settings.max_events_per_request} events per request",
        )

    received_at = datetime.now(timezone.utc)
    try:
        domain_events = [
            TrackingEvent.create(
                tenant_id=tenant_id,
                event_id=e.event_id,
                visitor_id=e.visitor_id,
                type=e.type.value,
                occurred_at=e.occurred_at or received_at,
                received_at=received_at,
                product_id=e.product_id,
                category=e.category,
                session_id=e.session_id,
                order_id=e.order_id,
                utm_source=e.utm_source,
                utm_medium=e.utm_medium,
                utm_campaign=e.utm_campaign,
                landing_page=e.landing_page,
                amount=e.amount,
            )
            for e in payload.events
        ]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        result = await use_case.execute(domain_events)
    except KafkaPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to publish event batch",
        ) from exc
    return CollectResponse(
        schema_version=TRACKING_EVENT_SCHEMA_VERSION,
        accepted=result.accepted,
        duplicates=result.duplicates,
        received_at=received_at,
    )


class OutboxStatusResponse(BaseModel):
    pending: int


class OutboxRelayResponse(BaseModel):
    relayed: int
    pending: int


@router.get("/ingestion/outbox/status", response_model=OutboxStatusResponse)
async def outbox_status(
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
) -> OutboxStatusResponse:
    pending = await SqlOutboxStore(session).pending_count(tenant_id)
    return OutboxStatusResponse(pending=pending)


@router.post("/ingestion/outbox/relay", response_model=OutboxRelayResponse)
async def relay_outbox(
    request: Request,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> OutboxRelayResponse:
    """보존된 outbox 이벤트를 Kafka 로 재발행한다(스케줄러/운영자가 호출).

    테넌트 스코프(인증 컨텍스트)로 동작한다. 시스템 전역 relay 는 테넌트별로 반복 호출.
    """
    producer = getattr(request.app.state, "kafka_producer", None)
    if producer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kafka producer is not ready",
        )
    store = SqlOutboxStore(session)
    relayed = await RelayOutbox(
        store, producer, batch=settings.ingestion_outbox_relay_batch
    ).run_once(tenant_id)
    pending = await store.pending_count(tenant_id)
    return OutboxRelayResponse(relayed=relayed, pending=pending)
