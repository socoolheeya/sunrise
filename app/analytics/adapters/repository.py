"""AnalyticsRepository 의 SQLAlchemy 집계 구현 (Outbound Adapter).

lite 버전은 events 원본 테이블을 직접 집계한다. 운영에서는 일 단위 사전집계
(Materialized View / 롤업 테이블)를 우선 조회해 대용량을 효율 처리한다.
테넌트 스코프 쿼리는 tenant_id 로 강제 격리하고, 벤치마크용 플랫폼 집계만
의도적으로 전체 테넌트를 익명 집계한다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.domain.model import MetricInputs
from app.analytics.domain.repository import AnalyticsRepository
from app.core.orm import EventRow


class SqlAnalyticsRepository(AnalyticsRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _window(self, start: datetime, end: datetime) -> ColumnElement[bool]:
        return and_(EventRow.occurred_at >= start, EventRow.occurred_at < end)

    def _tenant_window(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> ColumnElement[bool]:
        return and_(EventRow.tenant_id == tenant_id, self._window(start, end))

    async def _compute_inputs(self, base: ColumnElement[bool]) -> MetricInputs:
        purchase = and_(base, EventRow.type == "purchase")

        visitor_count = await self._session.scalar(
            select(func.count(func.distinct(EventRow.visitor_id))).where(base)
        )
        purchaser_count = await self._session.scalar(
            select(func.count(func.distinct(EventRow.visitor_id))).where(purchase)
        )
        purchase_count = await self._session.scalar(
            select(func.count()).select_from(EventRow).where(purchase)
        )
        revenue = await self._session.scalar(
            select(func.coalesce(func.sum(EventRow.amount), 0.0)).where(purchase)
        )
        repeat_subq = (
            select(EventRow.visitor_id)
            .where(purchase)
            .group_by(EventRow.visitor_id)
            .having(func.count() >= 2)
            .subquery()
        )
        repeat_count = await self._session.scalar(
            select(func.count()).select_from(repeat_subq)
        )

        return MetricInputs(
            visitor_count=int(visitor_count or 0),
            purchaser_count=int(purchaser_count or 0),
            purchase_count=int(purchase_count or 0),
            revenue=float(revenue or 0.0),
            repeat_purchaser_count=int(repeat_count or 0),
        )

    async def metric_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> MetricInputs:
        return await self._compute_inputs(self._tenant_window(tenant_id, start, end))

    async def platform_metric_inputs(
        self, start: datetime, end: datetime
    ) -> MetricInputs:
        return await self._compute_inputs(self._window(start, end))

    async def funnel_visitor_counts(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> dict[str, int]:
        rows = await self._session.execute(
            select(
                EventRow.type,
                func.count(func.distinct(EventRow.visitor_id)),
            )
            .where(self._tenant_window(tenant_id, start, end))
            .group_by(EventRow.type)
        )
        return {row[0]: int(row[1]) for row in rows.all()}

    async def purchase_months(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[tuple[str, str]]:
        purchase = and_(
            self._tenant_window(tenant_id, start, end),
            EventRow.type == "purchase",
        )
        rows = await self._session.execute(
            select(EventRow.visitor_id, EventRow.occurred_at).where(purchase)
        )
        # 월 버킷팅은 dialect 비의존성을 위해 애플리케이션에서 수행.
        return [(vid, dt.strftime("%Y-%m")) for vid, dt in rows.all()]
