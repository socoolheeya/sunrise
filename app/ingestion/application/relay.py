"""유스케이스: outbox relay.

Kafka 복구 시 보존된 이벤트를 재발행한다. 멱등(event_id)이므로 consumer 단에서
중복 제거되며, 한 건이라도 발행에 실패하면(아직 불안정) 중단하고 나머지는 보존한다.
"""

from __future__ import annotations

import json

from app.core.observability import IngestionMetrics, get_ingestion_metrics
from app.events.registry import TRACKING_EVENT_SCHEMA_VERSION
from app.ingestion.adapters.kafka import KafkaProducer
from app.ingestion.adapters.outbox import OutboxEntry, SqlOutboxStore


def _key(payload: dict) -> bytes:
    return f"{payload.get('tenant_id')}:{payload.get('visitor_id')}".encode("utf-8")


def _headers(payload: dict) -> list[tuple[str, bytes]]:
    return [
        ("schema_version", TRACKING_EVENT_SCHEMA_VERSION.encode("utf-8")),
        ("event_id", str(payload.get("event_id")).encode("utf-8")),
        ("tenant_id", str(payload.get("tenant_id")).encode("utf-8")),
    ]


class RelayOutbox:
    def __init__(
        self,
        store: SqlOutboxStore,
        producer: KafkaProducer,
        *,
        batch: int = 500,
        metrics: IngestionMetrics | None = None,
    ) -> None:
        self._store = store
        self._producer = producer
        self._batch = batch
        self._metrics = metrics or get_ingestion_metrics()

    async def run_once(self, tenant_id: str) -> int:
        entries = await self._store.claim(tenant_id, self._batch)
        relayed = 0
        for entry in entries:
            try:
                await self._publish(entry)
            except Exception as exc:  # Kafka 아직 불안정 → 중단(나머지 보존)
                await self._store.mark_failed([entry.id], str(exc))
                break
            await self._store.delete([entry.id])
            relayed += 1
        if relayed:
            self._metrics.record_outbox_relayed(relayed)
        return relayed

    async def _publish(self, entry: OutboxEntry) -> None:
        await self._producer.send_and_wait(
            entry.topic,
            value=json.dumps(entry.payload, separators=(",", ":")).encode("utf-8"),
            key=_key(entry.payload),
            headers=_headers(entry.payload),
        )
