"""EventRepository 의 SQLAlchemy 구현 (Outbound Adapter).

멱등 저장: 들어온 배치에서 (1) 배치 내 중복 제거 후, (2) 이미 저장된 event_id 를
조회로 걸러내고 신규만 INSERT 한다. SQLite/PostgreSQL 모두에서 동작하도록
dialect 특화 ON CONFLICT 대신 select-then-insert 방식을 쓴다.

운영 대용량에서는 PostgreSQL COPY + ON CONFLICT DO NOTHING, 혹은 Kafka 적재로
대체한다(상위 architecture.md 참고).
"""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import IngestionMetrics, get_ingestion_metrics
from app.core.orm import EventRow
from app.ingestion.adapters.clickhouse import ClickHouseEventMirror
from app.ingestion.domain.model import IngestResult, TrackingEvent
from app.ingestion.domain.repository import EventRepository


class SqlEventRepository(EventRepository):
    def __init__(
        self,
        session: AsyncSession,
        metrics: IngestionMetrics | None = None,
        mirror: ClickHouseEventMirror | None = None,
    ) -> None:
        self._session = session
        self._metrics = metrics or get_ingestion_metrics()
        self._mirror = mirror

    async def save_batch(self, events: list[TrackingEvent]) -> IngestResult:
        if not events:
            return IngestResult(accepted=0, duplicates=0)

        tenant_id = events[0].tenant_id

        # (1) 배치 내 중복 제거 (첫 항목 유지).
        unique: dict[str, TrackingEvent] = {}
        for e in events:
            unique.setdefault(e.event_id, e)
        incoming = len(events)

        values = [
            {
                "tenant_id": e.tenant_id,
                "event_id": e.event_id,
                "visitor_id": e.visitor_id,
                "type": e.type,
                "product_id": e.product_id,
                "category": e.category,
                "session_id": e.session_id,
                "order_id": e.order_id,
                "utm_source": e.utm_source,
                "utm_medium": e.utm_medium,
                "utm_campaign": e.utm_campaign,
                "landing_page": e.landing_page,
                "amount": e.amount,
                "occurred_at": e.occurred_at,
                "received_at": e.received_at,
            }
            for e in unique.values()
        ]
        accepted_ids = await self._insert_ignore_conflicts(values)

        accepted = len(accepted_ids)
        duplicates = incoming - accepted
        if self._mirror is not None:
            await self._mirror.mirror([e for eid, e in unique.items() if eid in accepted_ids])
        self._metrics.record_result(accepted=accepted, duplicates=duplicates)
        return IngestResult(accepted=accepted, duplicates=duplicates)

    async def _insert_ignore_conflicts(self, values: list[dict]) -> set[str]:
        dialect = self._session.bind.dialect.name if self._session.bind else ""
        if dialect in {"postgresql", "sqlite"}:
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            else:
                from sqlalchemy.dialects.sqlite import insert as dialect_insert

            stmt = (
                dialect_insert(EventRow)
                .values(values)
                .on_conflict_do_nothing(index_elements=["tenant_id", "event_id"])
                .returning(EventRow.event_id)
            )
            result = await self._session.execute(stmt)
            await self._session.commit()
            return {row[0] for row in result.all()}

        stmt = insert(EventRow).values(values)
        try:
            await self._session.execute(stmt)
            await self._session.commit()
            return {row["event_id"] for row in values}
        except IntegrityError:
            await self._session.rollback()
            return await self._fallback_insert_after_conflict(values)

    async def _fallback_insert_after_conflict(self, values: list[dict]) -> set[str]:
        tenant_id = values[0]["tenant_id"]
        ids = [row["event_id"] for row in values]
        existing = await self._session.execute(
            select(EventRow.event_id).where(
                EventRow.tenant_id == tenant_id,
                EventRow.event_id.in_(ids),
            )
        )
        existing_ids = {row[0] for row in existing.all()}
        retry_values = [row for row in values if row["event_id"] not in existing_ids]
        if not retry_values:
            return set()
        await self._session.execute(insert(EventRow).values(retry_values))
        await self._session.commit()
        return {row["event_id"] for row in retry_values}
