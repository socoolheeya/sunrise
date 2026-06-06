"""ClickHouse ingestion mirror adapter.

Compose/local 운영형 분석 검증을 위해 SQL 멱등 저장으로 확정된 신규 이벤트만
ClickHouse events table 에 복제한다. 대규모 운영 경로에서는 Kafka/Flink 적재가
주 경로이고, 이 어댑터는 로컬 통합/단순 배포용 보조 경로다.
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol

from app.ingestion.domain.model import TrackingEvent


class ClickHouseInsertClient(Protocol):
    def insert(
        self,
        table: str,
        data: list[tuple[Any, ...]],
        column_names: list[str],
    ) -> Any: ...


class ClickHouseEventMirror:
    def __init__(self, client: ClickHouseInsertClient, events_table: str) -> None:
        self._client = client
        self._events_table = events_table

    async def mirror(self, events: list[TrackingEvent]) -> None:
        if not events:
            return

        result = self._client.insert(
            self._events_table,
            data=[
                (
                    event.tenant_id,
                    event.event_id,
                    event.visitor_id,
                    event.type,
                    event.product_id,
                    event.category,
                    event.session_id,
                    event.order_id,
                    event.utm_source,
                    event.utm_medium,
                    event.utm_campaign,
                    event.landing_page,
                    event.amount,
                    event.occurred_at,
                    event.received_at,
                )
                for event in events
            ],
            column_names=[
                "tenant_id",
                "event_id",
                "visitor_id",
                "type",
                "product_id",
                "category",
                "session_id",
                "order_id",
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "landing_page",
                "amount",
                "occurred_at",
                "received_at",
            ],
        )
        if inspect.isawaitable(result):
            await result
