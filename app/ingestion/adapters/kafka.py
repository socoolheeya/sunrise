"""Kafka 기반 이벤트 수집 어댑터.

운영형 수집 모드에서 raw.events 토픽으로 이벤트를 발행한다. aiokafka 는 Kafka
모드에서만 lazy import 하므로 로컬 SQL 모드와 테스트는 외부 인프라 없이 동작한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.config import Settings
from app.core.observability import IngestionMetrics, get_ingestion_metrics
from app.events.registry import TRACKING_EVENT_SCHEMA_VERSION
from app.ingestion.domain.model import IngestResult, TrackingEvent
from app.ingestion.domain.repository import EventRepository


class KafkaProducer(Protocol):
    async def send_and_wait(
        self,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> Any: ...

    async def stop(self) -> None: ...


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(sep=" ", timespec="milliseconds")


def event_payload(event: TrackingEvent) -> dict[str, Any]:
    """Kafka Published Language payload for a tracking event."""
    return {
        "schema_version": TRACKING_EVENT_SCHEMA_VERSION,
        "tenant_id": event.tenant_id,
        "event_id": event.event_id,
        "visitor_id": event.visitor_id,
        "type": event.type,
        "product_id": event.product_id,
        "category": event.category,
        "session_id": event.session_id,
        "order_id": event.order_id,
        "utm_source": event.utm_source,
        "utm_medium": event.utm_medium,
        "utm_campaign": event.utm_campaign,
        "landing_page": event.landing_page,
        "amount": event.amount,
        "occurred_at": _serialize_datetime(event.occurred_at),
        "received_at": _serialize_datetime(event.received_at),
    }


def dlq_payload(event: TrackingEvent, error: Exception) -> dict[str, Any]:
    """DLQ Published Language payload for a failed tracking event publish."""
    return {
        "schema_version": TRACKING_EVENT_SCHEMA_VERSION,
        "failure_type": "kafka_publish_failed",
        "error": str(error),
        "event": event_payload(event),
    }


class KafkaPublishError(RuntimeError):
    """Raised when publishing a tracking event to Kafka fails."""


class OutboxSpool(Protocol):
    """Kafka 발행 실패 시 이벤트를 보존하는 내구성 backstop."""

    async def pending_count(self, tenant_id: str) -> int: ...

    async def enqueue(self, event: TrackingEvent, topic: str) -> None: ...


class KafkaEventRepository(EventRepository):
    """EventRepository 구현체: 배치 내부 중복 제거 후 Kafka 로 발행."""

    def __init__(
        self,
        producer: KafkaProducer,
        topic: str,
        *,
        dlq_topic: str | None = None,
        publish_attempts: int = 3,
        metrics: IngestionMetrics | None = None,
        outbox: OutboxSpool | None = None,
        outbox_max_pending: int = 100_000,
    ) -> None:
        self._producer = producer
        self._topic = topic
        self._dlq_topic = dlq_topic
        self._publish_attempts = max(1, publish_attempts)
        self._metrics = metrics or get_ingestion_metrics()
        self._outbox = outbox
        self._outbox_max_pending = outbox_max_pending

    async def save_batch(self, events: list[TrackingEvent]) -> IngestResult:
        if not events:
            return IngestResult(accepted=0, duplicates=0)

        unique: dict[str, TrackingEvent] = {}
        for event in events:
            unique.setdefault(event.event_id, event)

        for event in unique.values():
            await self._publish_event(event)

        accepted = len(unique)
        duplicates = len(events) - accepted
        self._metrics.record_result(accepted=accepted, duplicates=duplicates)
        return IngestResult(accepted=accepted, duplicates=duplicates)

    async def _publish_event(self, event: TrackingEvent) -> None:
        payload = event_payload(event)
        last_error: Exception | None = None
        for _ in range(self._publish_attempts):
            try:
                await self._producer.send_and_wait(
                    self._topic,
                    value=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                    key=f"{event.tenant_id}:{event.visitor_id}".encode("utf-8"),
                    headers=self._headers(event),
                )
                return
            except Exception as exc:
                last_error = exc

        error = last_error or RuntimeError("unknown Kafka publish failure")
        self._metrics.record_publish_failure()
        await self._publish_dlq(event, error)

        # 내구성 backstop: Kafka(+DLQ) 실패 시 outbox 에 보존하고 ack(at-least-once).
        # 단 backlog 이 상한을 넘으면 load shedding(503)으로 폭주를 막는다.
        if self._outbox is not None:
            if await self._outbox.pending_count(event.tenant_id) >= self._outbox_max_pending:
                self._metrics.record_outbox_backpressure()
                raise KafkaPublishError(
                    f"ingestion outbox backlog exceeded for tenant {event.tenant_id}"
                ) from error
            await self._outbox.enqueue(event, self._topic)
            self._metrics.record_outbox_enqueued()
            return

        raise KafkaPublishError(
            f"failed to publish event {event.event_id} to {self._topic}"
        ) from error

    async def _publish_dlq(self, event: TrackingEvent, error: Exception) -> None:
        if self._dlq_topic is None:
            return

        try:
            await self._producer.send_and_wait(
                self._dlq_topic,
                value=json.dumps(
                    dlq_payload(event, error), separators=(",", ":")
                ).encode("utf-8"),
                key=f"{event.tenant_id}:{event.event_id}".encode("utf-8"),
                headers=self._headers(event),
            )
            self._metrics.record_dlq_published()
        except Exception as exc:
            self._metrics.record_dlq_failure()

    def _headers(self, event: TrackingEvent) -> list[tuple[str, bytes]]:
        return [
            ("schema_version", TRACKING_EVENT_SCHEMA_VERSION.encode("utf-8")),
            ("event_id", event.event_id.encode("utf-8")),
            ("tenant_id", event.tenant_id.encode("utf-8")),
        ]


async def create_aiokafka_producer(settings: Settings) -> KafkaProducer:
    try:
        from aiokafka import AIOKafkaProducer
    except ImportError as exc:
        raise RuntimeError(
            "Kafka ingestion mode requires aiokafka. Install aiokafka or use "
            "SUNRISE_INGESTION_SINK=sql."
        ) from exc

    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        acks=settings.kafka_acks,
        compression_type=settings.kafka_compression_type,
        linger_ms=settings.kafka_linger_ms,
        request_timeout_ms=settings.kafka_request_timeout_ms,
        retry_backoff_ms=settings.kafka_retry_backoff_ms,
        max_batch_size=settings.kafka_max_batch_size,
    )
    await producer.start()
    return producer
