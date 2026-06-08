"""AnalyticsRepository 의 SQLAlchemy 집계 구현 (Outbound Adapter).

lite 버전은 events 원본 테이블을 직접 집계한다. 운영에서는 일 단위 사전집계
(Materialized View / 롤업 테이블)를 우선 조회해 대용량을 효율 처리한다.
테넌트 스코프 쿼리는 tenant_id 로 강제 격리하고, 벤치마크용 플랫폼 집계만
의도적으로 전체 테넌트를 익명 집계한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import asdict

from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.domain.cohort import build_cohort_rows, cohort_event_types
from app.analytics.domain.model import (
    AttributionChannel,
    CohortCell,
    CohortReport,
    CohortRow,
    DataTalkSnapshot,
    InflowChannel,
    LifecycleSegment,
    MetricInputs,
    RevenueBreakdown,
    VisitorLifecycleInput,
    purchase_segment,
    visit_segment,
)
from app.analytics.domain.order_fact import OrderEvent, fold_order_facts
from app.analytics.domain.repository import AnalyticsRepository
from app.core.orm import (
    CohortRetentionRow,
    CustomerSegmentDailyRow,
    DataTalkSnapshotRow,
    EventRow,
    OrderFactRow,
)


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

    async def attribution_channels(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> list[AttributionChannel]:
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.order_id,
                EventRow.type,
                EventRow.amount,
                EventRow.occurred_at,
                EventRow.utm_medium,
                EventRow.utm_source,
                EventRow.category,
            ).where(self._tenant_window(tenant_id, start, end))
        )
        touches_by_visitor: dict[str, list[tuple[datetime, str]]] = {}
        purchases: dict[str, tuple[str, datetime, float]] = {}
        fallback_index = 0
        for (
            visitor_id,
            order_id,
            event_type,
            amount,
            occurred_at,
            utm_medium,
            utm_source,
            category,
        ) in rows.all():
            channel = utm_medium or utm_source or category or "unknown"
            if event_type in {"campaign_impression", "campaign_click", "campaign_open"}:
                touches_by_visitor.setdefault(visitor_id, []).append((occurred_at, channel))
            if event_type == "purchase":
                key = order_id or f"event:{fallback_index}"
                fallback_index += 1
                purchases.setdefault(key, (visitor_id, occurred_at, float(amount or 0.0)))

        window_seconds = attribution_window_hours * 3600
        touchpoint_counts: dict[str, int] = {}
        purchaser_sets: dict[str, set[str]] = {}
        purchase_counts: dict[str, int] = {}
        revenue: dict[str, float] = {}
        for visitor_id, touches in touches_by_visitor.items():
            for _, channel in touches:
                touchpoint_counts[channel] = touchpoint_counts.get(channel, 0) + 1
            touches.sort(key=lambda item: item[0])

        for visitor_id, purchased_at, amount in purchases.values():
            candidates = [
                (touched_at, channel)
                for touched_at, channel in touches_by_visitor.get(visitor_id, [])
                if 0 <= (purchased_at - touched_at).total_seconds() <= window_seconds
            ]
            if not candidates:
                continue
            _, channel = max(candidates, key=lambda item: item[0])
            purchaser_sets.setdefault(channel, set()).add(visitor_id)
            purchase_counts[channel] = purchase_counts.get(channel, 0) + 1
            revenue[channel] = revenue.get(channel, 0.0) + amount

        channels = sorted(
            set(touchpoint_counts) | set(revenue),
            key=lambda channel: revenue.get(channel, 0.0),
            reverse=True,
        )
        return [
            AttributionChannel(
                channel=channel,
                touchpoint_count=touchpoint_counts.get(channel, 0),
                purchaser_count=len(purchaser_sets.get(channel, set())),
                purchase_count=purchase_counts.get(channel, 0),
                revenue=revenue.get(channel, 0.0),
                model="last_touch",
            )
            for channel in channels
        ]

    async def save_datatalk_snapshot(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        snapshot: DataTalkSnapshot,
    ) -> None:
        values = {
            "tenant_id": tenant_id,
            "snapshot_id": snapshot.snapshot_id,
            "start_at": start,
            "end_at": end,
            "status": snapshot.status,
            "payload_json": json.dumps(asdict(snapshot.report), default=str),
            "generated_at": snapshot.generated_at,
            "created_at": datetime.now(tz=snapshot.generated_at.tzinfo),
        }
        bind = self._session.get_bind()
        if bind.dialect.name == "sqlite":
            stmt = sqlite_insert(DataTalkSnapshotRow).values(**values)
        else:
            stmt = pg_insert(DataTalkSnapshotRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "snapshot_id"],
            set_={
                "status": values["status"],
                "payload_json": values["payload_json"],
                "generated_at": values["generated_at"],
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def refresh_order_facts(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> int:
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.order_id,
                EventRow.type,
                EventRow.amount,
                EventRow.occurred_at,
                EventRow.session_id,
                EventRow.utm_medium,
                EventRow.utm_source,
                EventRow.category,
            ).where(self._tenant_window(tenant_id, start, end))
        )
        events = [
            OrderEvent(
                visitor_id=visitor_id,
                order_id=order_id,
                type=event_type,
                amount=float(amount or 0.0),
                occurred_at=occurred_at,
                session_id=session_id,
                utm_medium=utm_medium,
                utm_source=utm_source,
                category=category,
            )
            for (
                visitor_id,
                order_id,
                event_type,
                amount,
                occurred_at,
                session_id,
                utm_medium,
                utm_source,
                category,
            ) in rows.all()
        ]
        facts = fold_order_facts(
            tenant_id, events, attribution_window_hours=attribution_window_hours
        )
        now = datetime.now(tz=timezone.utc)
        bind = self._session.get_bind()
        insert = sqlite_insert if bind.dialect.name == "sqlite" else pg_insert
        for fact in facts:
            values = {
                "tenant_id": fact.tenant_id,
                "order_id": fact.order_id,
                "visitor_id": fact.visitor_id,
                "amount": fact.amount,
                "status": fact.status,
                "channel": fact.channel,
                "onsite_matched": fact.onsite_matched,
                "attributed": fact.attributed,
                "attributed_channel": fact.attributed_channel,
                "occurred_at": fact.occurred_at,
                "created_at": now,
                "updated_at": now,
            }
            stmt = insert(OrderFactRow).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["tenant_id", "order_id"],
                set_={
                    "visitor_id": values["visitor_id"],
                    "amount": values["amount"],
                    "status": values["status"],
                    "channel": values["channel"],
                    "onsite_matched": values["onsite_matched"],
                    "attributed": values["attributed"],
                    "attributed_channel": values["attributed_channel"],
                    "occurred_at": values["occurred_at"],
                    "updated_at": values["updated_at"],
                },
            )
            await self._session.execute(stmt)
        await self._session.commit()
        return len(facts)

    async def order_revenue_breakdown(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        rows = await self._session.execute(
            select(
                OrderFactRow.amount,
                OrderFactRow.status,
                OrderFactRow.onsite_matched,
                OrderFactRow.attributed,
            ).where(
                and_(
                    OrderFactRow.tenant_id == tenant_id,
                    OrderFactRow.occurred_at >= start,
                    OrderFactRow.occurred_at < end,
                )
            )
        )
        total = 0.0
        onsite = 0.0
        attributed = 0.0
        for amount, status, onsite_matched, is_attributed in rows.all():
            if status != "completed":
                continue
            value = float(amount or 0.0)
            total += value
            if onsite_matched:
                onsite += value
            if is_attributed:
                attributed += value
        return RevenueBreakdown(
            total_revenue=total,
            onsite_revenue=onsite,
            attributed_revenue=attributed,
        )

    async def refresh_lifecycle_segments(
        self, tenant_id: str, start: datetime, end: datetime, as_of: datetime
    ) -> int:
        inputs = await self.lifecycle_inputs(tenant_id, start, end)
        now = datetime.now(tz=timezone.utc)
        bind = self._session.get_bind()
        insert = sqlite_insert if bind.dialect.name == "sqlite" else pg_insert
        for item in inputs:
            values = {
                "tenant_id": tenant_id,
                "customer_id": item.visitor_id,
                "as_of": as_of,
                "visit_segment": visit_segment(as_of, item.last_seen_at),
                "purchase_segment": purchase_segment(
                    as_of, item.purchase_count, item.last_purchase_at
                ),
                "revenue": item.revenue,
                "created_at": now,
                "updated_at": now,
            }
            stmt = insert(CustomerSegmentDailyRow).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["tenant_id", "customer_id", "as_of"],
                set_={
                    "visit_segment": values["visit_segment"],
                    "purchase_segment": values["purchase_segment"],
                    "revenue": values["revenue"],
                    "updated_at": values["updated_at"],
                },
            )
            await self._session.execute(stmt)
        await self._session.commit()
        return len(inputs)

    async def segment_snapshot(
        self, tenant_id: str, as_of: datetime
    ) -> list[LifecycleSegment]:
        rows = await self._session.execute(
            select(
                CustomerSegmentDailyRow.customer_id,
                CustomerSegmentDailyRow.visit_segment,
                CustomerSegmentDailyRow.purchase_segment,
                CustomerSegmentDailyRow.revenue,
            ).where(
                and_(
                    CustomerSegmentDailyRow.tenant_id == tenant_id,
                    CustomerSegmentDailyRow.as_of == as_of,
                )
            )
        )
        return [
            LifecycleSegment(
                visitor_id=customer_id,
                visit_segment=vseg,
                purchase_segment=pseg,
                revenue=float(revenue or 0.0),
            )
            for customer_id, vseg, pseg, revenue in rows.all()
        ]

    async def refresh_cohort_retention(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        cohort_type: str,
        granularity: str,
        max_offset: int,
    ) -> int:
        types = cohort_event_types(cohort_type)
        rows = await self._session.execute(
            select(EventRow.visitor_id, EventRow.occurred_at).where(
                and_(
                    self._tenant_window(tenant_id, start, end),
                    EventRow.type.in_(types),
                )
            )
        )
        records = [(visitor_id, occurred_at) for visitor_id, occurred_at in rows.all()]
        cohort_rows = build_cohort_rows(
            records, granularity=granularity, max_offset=max_offset
        )
        # 재머티리얼라이즈는 셀 수가 줄어들 수 있으므로 기존 셀을 지우고 다시 쓴다.
        await self._session.execute(
            delete(CohortRetentionRow).where(
                and_(
                    CohortRetentionRow.tenant_id == tenant_id,
                    CohortRetentionRow.cohort_type == cohort_type,
                    CohortRetentionRow.granularity == granularity,
                )
            )
        )
        now = datetime.now(tz=timezone.utc)
        cell_count = 0
        for row in cohort_rows:
            for cell in row.cells:
                self._session.add(
                    CohortRetentionRow(
                        tenant_id=tenant_id,
                        cohort_type=cohort_type,
                        granularity=granularity,
                        cohort=row.cohort,
                        offset=cell.offset,
                        base_count=row.size,
                        retained_count=cell.active,
                        retention_rate=cell.rate,
                        created_at=now,
                    )
                )
                cell_count += 1
        await self._session.commit()
        return cell_count

    async def cohort_retention(
        self, tenant_id: str, cohort_type: str, granularity: str
    ) -> CohortReport:
        rows = await self._session.execute(
            select(
                CohortRetentionRow.cohort,
                CohortRetentionRow.offset,
                CohortRetentionRow.base_count,
                CohortRetentionRow.retained_count,
                CohortRetentionRow.retention_rate,
            )
            .where(
                and_(
                    CohortRetentionRow.tenant_id == tenant_id,
                    CohortRetentionRow.cohort_type == cohort_type,
                    CohortRetentionRow.granularity == granularity,
                )
            )
            .order_by(CohortRetentionRow.cohort, CohortRetentionRow.offset)
        )
        by_cohort: dict[str, tuple[int, list[CohortCell]]] = {}
        for cohort, offset, base_count, retained_count, rate in rows.all():
            _, cells = by_cohort.setdefault(cohort, (base_count, []))
            cells.append(CohortCell(offset=offset, active=retained_count, rate=rate))
        report_rows = tuple(
            CohortRow(cohort=cohort, size=size, cells=tuple(cells))
            for cohort, (size, cells) in by_cohort.items()
        )
        return CohortReport(rows=report_rows)
