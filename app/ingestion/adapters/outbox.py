"""수집 outbox 저장소 (Outbound Adapter).

Kafka 발행 실패 시 이벤트(Published Language payload)를 관계형 테이블에 보존하고,
복구 후 relay 가 재발행한다. (tenant_id, event_id) 유니크로 중복 적재를 막는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import IngestionOutboxRow
from app.ingestion.adapters.kafka import event_payload
from app.ingestion.domain.model import TrackingEvent


@dataclass(frozen=True)
class OutboxEntry:
    id: int
    tenant_id: str
    event_id: str
    topic: str
    payload: dict[str, Any]


class SqlOutboxStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _insert(self):
        bind = self._session.get_bind()
        return sqlite_insert if bind.dialect.name == "sqlite" else pg_insert

    async def enqueue(self, event: TrackingEvent, topic: str) -> None:
        """이벤트를 outbox 에 멱등 적재한다(이미 존재하면 무시)."""
        values = {
            "tenant_id": event.tenant_id,
            "event_id": event.event_id,
            "topic": topic,
            "payload_json": json.dumps(event_payload(event), separators=(",", ":")),
            "attempts": 0,
            "created_at": datetime.now(tz=timezone.utc),
        }
        stmt = self._insert()(IngestionOutboxRow).values(**values)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "event_id"]
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def pending_count(self, tenant_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(IngestionOutboxRow)
            .where(IngestionOutboxRow.tenant_id == tenant_id)
        )
        return int(result.scalar_one())

    async def claim(self, tenant_id: str, limit: int) -> list[OutboxEntry]:
        rows = await self._session.execute(
            select(
                IngestionOutboxRow.id,
                IngestionOutboxRow.tenant_id,
                IngestionOutboxRow.event_id,
                IngestionOutboxRow.topic,
                IngestionOutboxRow.payload_json,
            )
            .where(IngestionOutboxRow.tenant_id == tenant_id)
            .order_by(IngestionOutboxRow.id)
            .limit(limit)
        )
        return [
            OutboxEntry(
                id=row_id,
                tenant_id=tid,
                event_id=eid,
                topic=topic,
                payload=json.loads(payload_json),
            )
            for row_id, tid, eid, topic, payload_json in rows.all()
        ]

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            return
        await self._session.execute(
            delete(IngestionOutboxRow).where(IngestionOutboxRow.id.in_(ids))
        )
        await self._session.commit()

    async def mark_failed(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        await self._session.execute(
            update(IngestionOutboxRow)
            .where(IngestionOutboxRow.id.in_(ids))
            .values(
                attempts=IngestionOutboxRow.attempts + 1,
                last_error=error[:1000],
                last_attempt_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._session.commit()
