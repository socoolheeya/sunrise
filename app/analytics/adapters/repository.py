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

from app.analytics.domain.model import (
    InflowChannel,
    MetricInputs,
    RevenueBreakdown,
    VisitorLifecycleInput,
)
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
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.session_id,
                EventRow.order_id,
                EventRow.type,
                EventRow.amount,
            ).where(base)
        )
        visitors: set[str] = set()
        sessions: set[str] = set()
        purchase_orders: dict[str, tuple[str, float]] = {}
        fallback_index = 0
        for visitor_id, session_id, order_id, event_type, amount in rows.all():
            visitors.add(visitor_id)
            if session_id:
                sessions.add(session_id)
            if event_type != "purchase":
                continue
            key = order_id or f"event:{fallback_index}"
            fallback_index += 1
            purchase_orders.setdefault(key, (visitor_id, float(amount or 0.0)))

        purchasers = {visitor_id for visitor_id, _ in purchase_orders.values()}
        order_count_by_visitor: dict[str, int] = {}
        for visitor_id, _ in purchase_orders.values():
            order_count_by_visitor[visitor_id] = order_count_by_visitor.get(visitor_id, 0) + 1
        repeat_count = sum(1 for count in order_count_by_visitor.values() if count >= 2)

        return MetricInputs(
            visitor_count=len(visitors),
            purchaser_count=len(purchasers),
            purchase_count=len(purchase_orders),
            revenue=sum(amount for _, amount in purchase_orders.values()),
            repeat_purchaser_count=repeat_count,
            session_count=len(sessions) or len(visitors),
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

    async def inflow_channels(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[InflowChannel]:
        rows = await self._session.execute(
            select(
                EventRow.category,
                EventRow.session_id,
                EventRow.utm_source,
                EventRow.utm_medium,
                EventRow.visitor_id,
                EventRow.order_id,
                EventRow.type,
                EventRow.amount,
            ).where(self._tenant_window(tenant_id, start, end))
        )
        sessions: dict[str, set[str]] = {}
        visitors: dict[str, set[str]] = {}
        purchasers: dict[str, set[str]] = {}
        purchases: dict[str, dict[str, float]] = {}
        fallback_index = 0
        for (
            category,
            session_id,
            utm_source,
            utm_medium,
            visitor_id,
            order_id,
            event_type,
            amount,
        ) in rows.all():
            channel = utm_medium or utm_source or category or "unknown"
            if session_id:
                sessions.setdefault(channel, set()).add(session_id)
            visitors.setdefault(channel, set()).add(visitor_id)
            if event_type == "purchase":
                purchasers.setdefault(channel, set()).add(visitor_id)
                key = order_id or f"event:{fallback_index}"
                fallback_index += 1
                purchases.setdefault(channel, {}).setdefault(key, float(amount or 0.0))
        return [
            InflowChannel(
                channel=channel,
                session_count=len(sessions.get(channel, set())) or len(visitor_ids),
                visitor_count=len(visitor_ids),
                purchaser_count=len(purchasers.get(channel, set())),
                purchase_count=len(purchases.get(channel, {})),
                revenue=sum(purchases.get(channel, {}).values()),
            )
            for channel, visitor_ids in visitors.items()
        ]

    async def revenue_breakdown(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.order_id,
                EventRow.type,
                EventRow.amount,
            ).where(self._tenant_window(tenant_id, start, end))
        )
        touched_visitors: set[str] = set()
        clicked_visitors: set[str] = set()
        purchases: dict[str, tuple[str, float]] = {}
        fallback_index = 0
        for visitor_id, order_id, event_type, amount in rows.all():
            if event_type in {"campaign_impression", "campaign_click"}:
                touched_visitors.add(visitor_id)
            if event_type == "campaign_click":
                clicked_visitors.add(visitor_id)
            if event_type == "purchase":
                key = order_id or f"event:{fallback_index}"
                fallback_index += 1
                purchases.setdefault(key, (visitor_id, float(amount or 0.0)))
        total = sum(amount for _, amount in purchases.values())
        onsite = sum(
            amount
            for visitor_id, amount in purchases.values()
            if visitor_id in touched_visitors
        )
        attributed = sum(
            amount
            for visitor_id, amount in purchases.values()
            if visitor_id in clicked_visitors
        )
        return RevenueBreakdown(
            total_revenue=total,
            onsite_revenue=onsite,
            attributed_revenue=attributed,
        )

    async def lifecycle_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[VisitorLifecycleInput]:
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.type,
                EventRow.amount,
                EventRow.occurred_at,
            ).where(self._tenant_window(tenant_id, start, end))
        )
        state: dict[str, dict[str, object]] = {}
        for visitor_id, event_type, amount, occurred_at in rows.all():
            current = state.setdefault(
                visitor_id,
                {
                    "view_count": 0,
                    "purchase_count": 0,
                    "revenue": 0.0,
                    "last_seen_at": None,
                    "last_purchase_at": None,
                },
            )
            if event_type in {"view", "category_view", "cart_add", "cart_remove"}:
                current["view_count"] = int(current["view_count"]) + 1
                last_seen = current["last_seen_at"]
                if last_seen is None or occurred_at > last_seen:
                    current["last_seen_at"] = occurred_at
            if event_type == "purchase":
                current["purchase_count"] = int(current["purchase_count"]) + 1
                current["revenue"] = float(current["revenue"]) + float(amount or 0.0)
                last_purchase = current["last_purchase_at"]
                if last_purchase is None or occurred_at > last_purchase:
                    current["last_purchase_at"] = occurred_at
                last_seen = current["last_seen_at"]
                if last_seen is None or occurred_at > last_seen:
                    current["last_seen_at"] = occurred_at
        return [
            VisitorLifecycleInput(
                visitor_id=visitor_id,
                view_count=int(values["view_count"]),
                purchase_count=int(values["purchase_count"]),
                revenue=float(values["revenue"]),
                last_seen_at=values["last_seen_at"],
                last_purchase_at=values["last_purchase_at"],
            )
            for visitor_id, values in state.items()
        ]
