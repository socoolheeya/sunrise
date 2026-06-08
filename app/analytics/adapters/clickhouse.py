"""ClickHouse 기반 AnalyticsRepository 구현.

운영형 분석 모드에서 raw/event read model 또는 materialized view 테이블을 조회한다.
clickhouse-connect 는 ClickHouse 모드에서만 lazy import 하므로 로컬 SQL 모드와
테스트는 외부 OLAP 인프라 없이 동작한다.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Protocol

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
from app.analytics.domain.order_fact import (
    OrderEvent,
    OrderFact,
    fold_order_facts,
    revenue_breakdown_from_facts,
)
from app.analytics.domain.repository import AnalyticsRepository


class ClickHouseClient(Protocol):
    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> Any: ...

    def command(self, sql: str, parameters: dict[str, Any] | None = None) -> Any: ...

    def insert(
        self, table: str, data: list[list[Any]], column_names: list[str]
    ) -> Any: ...


class ClickHouseQueryError(RuntimeError):
    """Raised when the ClickHouse backend is unavailable or rejects a query."""


# read-model 테이블 컬럼 계약 (refresh insert 시 사용)
_ORDER_FACT_COLUMNS = [
    "tenant_id", "order_id", "visitor_id", "amount", "status", "channel",
    "onsite_matched", "attributed", "attributed_channel", "occurred_at", "computed_at",
]
_SEGMENT_COLUMNS = [
    "tenant_id", "customer_id", "as_of", "visit_segment", "purchase_segment",
    "revenue", "computed_at",
]
_COHORT_COLUMNS = [
    "tenant_id", "cohort_type", "granularity", "cohort", "offset",
    "base_count", "retained_count", "retention_rate", "computed_at",
]


async def _command(
    client: ClickHouseClient, sql: str, parameters: dict[str, Any]
) -> None:
    try:
        result = client.command(sql, parameters=parameters)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        raise ClickHouseQueryError("ClickHouse command failed") from exc


async def _insert(
    client: ClickHouseClient,
    table: str,
    rows: list[list[Any]],
    column_names: list[str],
) -> None:
    if not rows:
        return
    try:
        result = client.insert(table, rows, column_names=column_names)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        raise ClickHouseQueryError("ClickHouse insert failed") from exc


async def _query_rows(
    client: ClickHouseClient, sql: str, parameters: dict[str, Any]
) -> list[dict[str, Any]]:
    try:
        result = client.query(sql, parameters=parameters)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, list):
            return [dict(row) for row in result]

        if hasattr(result, "named_results"):
            return [dict(row) for row in result.named_results()]

        rows = getattr(result, "result_rows", [])
        columns = getattr(result, "column_names", [])
        return [dict(zip(columns, row, strict=False)) for row in rows]
    except Exception as exc:
        raise ClickHouseQueryError("ClickHouse query failed") from exc


def _first_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[0] if rows else {}


class ClickHouseAnalyticsRepository(AnalyticsRepository):
    def __init__(
        self,
        client: ClickHouseClient,
        events_table: str,
        metric_daily_table: str | None = None,
        snapshot_session: AsyncSession | None = None,
    ) -> None:
        self._client = client
        self._events_table = events_table
        self._metric_daily_table = metric_daily_table
        # read-model 테이블은 events_table 과 동일 DB 스키마에 둔다.
        # (예: events_table="sunrise.events" → "sunrise.order_facts")
        prefix = events_table.rsplit(".", 1)[0] + "." if "." in events_table else ""
        self._order_facts_table = f"{prefix}order_facts"
        self._segment_table = f"{prefix}customer_segment_daily"
        self._cohort_table = f"{prefix}cohort_retention"
        # DataTalk snapshot 은 저용량 frozen 리포트 문서다. OLAP(ClickHouse)는
        # 분석 조회용이고, snapshot 은 관계형 저장소(migration 0005 의
        # datatalk_snapshots 테이블)에 영속화한다. ClickHouse 모드에서도 동일
        # 관계형 세션을 주입받아 SQL 저장소로 위임한다.
        self._snapshot_session = snapshot_session

    @property
    def _events_relation(self) -> str:
        return f"""
            (
                SELECT
                    tenant_id,
                    event_id,
                    argMax(visitor_id, received_at) AS visitor_id,
                    argMax(type, received_at) AS type,
                    argMax(product_id, received_at) AS product_id,
                    argMax(category, received_at) AS category,
                    argMax(session_id, received_at) AS session_id,
                    argMax(order_id, received_at) AS order_id,
                    argMax(utm_source, received_at) AS utm_source,
                    argMax(utm_medium, received_at) AS utm_medium,
                    argMax(utm_campaign, received_at) AS utm_campaign,
                    argMax(landing_page, received_at) AS landing_page,
                    argMax(amount, received_at) AS amount,
                    argMax(occurred_at, received_at) AS occurred_at,
                    max(received_at) AS latest_received_at
                FROM {self._events_table}
                GROUP BY tenant_id, event_id
            )
        """

    async def metric_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> MetricInputs:
        return await self._metric_inputs(
            "tenant_id = {tenant_id:String}",
            {"tenant_id": tenant_id, "start": start, "end": end},
        )

    async def platform_metric_inputs(
        self, start: datetime, end: datetime
    ) -> MetricInputs:
        return await self._metric_inputs("1 = 1", {"start": start, "end": end})

    async def _metric_inputs_from_daily(
        self, tenant_filter: str, params: dict[str, Any]
    ) -> MetricInputs:
        # 주의: agg_metric_daily_v2 는 purchase_count/revenue 를 '구매 이벤트'
        # 단위로 집계한다(countIf/sumIf). PRD §4.1/§4.2 는 order_id 기준 '주문
        # 단위' 중복제거를 강제하므로, 한 주문이 여러 purchase 이벤트를 내면 이
        # 사전집계 경로는 매출/주문수를 과대 집계한다. 따라서 tenant metrics 는
        # 항상 _metric_inputs(raw-scan, 주문 dedup) 를 사용하고 이 메서드는
        # 호출하지 않는다. 주문 단위 사전집계가 필요하면 order_fact read model
        # (architecture §6.7) 를 먼저 도입해야 한다.
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                uniqMerge(visitor_state) AS visitor_count,
                uniqMerge(purchaser_state) AS purchaser_count,
                coalesce(sum(purchase_count), 0) AS purchase_count,
                coalesce(sum(revenue), 0) AS revenue
            FROM {self._metric_daily_table}
            WHERE {tenant_filter}
              AND period >= toDate({{start:DateTime}})
              AND period < toDate({{end:DateTime}})
            """,
            params,
        )
        repeat_rows = await _query_rows(
            self._client,
            f"""
            SELECT count() AS repeat_purchaser_count
            FROM (
                SELECT visitor_id
                FROM {self._events_relation}
                WHERE {tenant_filter}
                  AND occurred_at >= {{start:DateTime}}
                  AND occurred_at < {{end:DateTime}}
                  AND type = 'purchase'
                GROUP BY visitor_id
                HAVING count() >= 2
            )
            """,
            params,
        )
        row = _first_row(rows)
        repeat_row = _first_row(repeat_rows)
        return MetricInputs(
            visitor_count=int(row.get("visitor_count") or 0),
            purchaser_count=int(row.get("purchaser_count") or 0),
            purchase_count=int(row.get("purchase_count") or 0),
            revenue=float(row.get("revenue") or 0.0),
            repeat_purchaser_count=int(repeat_row.get("repeat_purchaser_count") or 0),
        )

    async def _metric_inputs(
        self, tenant_filter: str, params: dict[str, Any]
    ) -> MetricInputs:
        base_filter = (
            f"{tenant_filter} "
            "AND occurred_at >= {start:DateTime} "
            "AND occurred_at < {end:DateTime}"
        )
        rows = await _query_rows(
            self._client,
            f"""
            SELECT visitor_id, session_id, order_id, type, amount
            FROM {self._events_relation}
            WHERE {base_filter}
            """,
            params,
        )
        visitors: set[str] = set()
        sessions: set[str] = set()
        purchase_orders: dict[str, tuple[str, float]] = {}
        fallback_index = 0
        for row in rows:
            visitor_id = str(row["visitor_id"])
            visitors.add(visitor_id)
            session_id = row.get("session_id")
            if session_id:
                sessions.add(str(session_id))
            if row.get("type") != "purchase":
                continue
            order_id = row.get("order_id")
            key = str(order_id) if order_id else f"event:{fallback_index}"
            fallback_index += 1
            purchase_orders.setdefault(key, (visitor_id, float(row.get("amount") or 0.0)))
        purchasers = {visitor_id for visitor_id, _ in purchase_orders.values()}
        order_count_by_visitor: dict[str, int] = {}
        for visitor_id, _ in purchase_orders.values():
            order_count_by_visitor[visitor_id] = order_count_by_visitor.get(visitor_id, 0) + 1
        return MetricInputs(
            visitor_count=len(visitors),
            purchaser_count=len(purchasers),
            purchase_count=len(purchase_orders),
            revenue=sum(amount for _, amount in purchase_orders.values()),
            repeat_purchaser_count=sum(
                1 for count in order_count_by_visitor.values() if count >= 2
            ),
            session_count=len(sessions) or len(visitors),
        )

    async def funnel_visitor_counts(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> dict[str, int]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT type, uniqExact(visitor_id) AS visitors
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            GROUP BY type
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        return {str(row["type"]): int(row["visitors"]) for row in rows}

    async def purchase_months(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[tuple[str, str]]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                visitor_id,
                formatDateTime(occurred_at, '%Y-%m') AS period
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND type = 'purchase'
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        return [(str(row["visitor_id"]), str(row["period"])) for row in rows]

    async def inflow_channels(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[InflowChannel]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                category,
                session_id,
                utm_source,
                utm_medium,
                visitor_id,
                order_id,
                type,
                amount
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        sessions: dict[str, set[str]] = {}
        visitors: dict[str, set[str]] = {}
        purchasers: dict[str, set[str]] = {}
        purchases: dict[str, dict[str, float]] = {}
        fallback_index = 0
        for row in rows:
            channel = (
                row.get("utm_medium")
                or row.get("utm_source")
                or row.get("category")
                or "unknown"
            )
            channel = str(channel)
            session_id = row.get("session_id")
            visitor_id = str(row["visitor_id"])
            if session_id:
                sessions.setdefault(channel, set()).add(str(session_id))
            visitors.setdefault(channel, set()).add(visitor_id)
            if row.get("type") == "purchase":
                purchasers.setdefault(channel, set()).add(visitor_id)
                order_id = row.get("order_id")
                key = str(order_id) if order_id else f"event:{fallback_index}"
                fallback_index += 1
                purchases.setdefault(channel, {}).setdefault(
                    key, float(row.get("amount") or 0.0)
                )
        return [
            InflowChannel(
                channel=channel,
                session_count=(
                    len(sessions.get(channel, set()))
                    or len(visitor_ids)
                ),
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
        rows = await _query_rows(
            self._client,
            f"""
            SELECT visitor_id, order_id, type, amount
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        touched_visitors: set[str] = set()
        clicked_visitors: set[str] = set()
        purchases: dict[str, tuple[str, float]] = {}
        fallback_index = 0
        for row in rows:
            visitor_id = str(row["visitor_id"])
            event_type = str(row["type"])
            if event_type in {"campaign_impression", "campaign_click"}:
                touched_visitors.add(visitor_id)
            if event_type == "campaign_click":
                clicked_visitors.add(visitor_id)
            if event_type == "purchase":
                order_id = row.get("order_id")
                key = str(order_id) if order_id else f"event:{fallback_index}"
                fallback_index += 1
                purchases.setdefault(key, (visitor_id, float(row.get("amount") or 0.0)))
        total = sum(amount for _, amount in purchases.values())
        return RevenueBreakdown(
            total_revenue=total,
            onsite_revenue=sum(
                amount
                for visitor_id, amount in purchases.values()
                if visitor_id in touched_visitors
            ),
            attributed_revenue=sum(
                amount
                for visitor_id, amount in purchases.values()
                if visitor_id in clicked_visitors
            ),
        )

    async def lifecycle_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[VisitorLifecycleInput]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                visitor_id,
                countIf(type IN ('view', 'category_view', 'cart_add', 'cart_remove')) AS view_count,
                countIf(type = 'purchase') AS purchase_count,
                coalesce(sumIf(amount, type = 'purchase'), 0) AS revenue,
                max(occurred_at) AS last_seen_at,
                maxIf(occurred_at, type = 'purchase') AS last_purchase_at
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            GROUP BY visitor_id
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        return [
            VisitorLifecycleInput(
                visitor_id=str(row["visitor_id"]),
                view_count=int(row.get("view_count") or 0),
                purchase_count=int(row.get("purchase_count") or 0),
                revenue=float(row.get("revenue") or 0.0),
                last_seen_at=row.get("last_seen_at"),
                last_purchase_at=row.get("last_purchase_at"),
            )
            for row in rows
        ]

    async def attribution_channels(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> list[AttributionChannel]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                visitor_id,
                order_id,
                type,
                amount,
                occurred_at,
                utm_medium,
                utm_source,
                category
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        touches_by_visitor: dict[str, list[tuple[datetime, str]]] = {}
        purchases: dict[str, tuple[str, datetime, float]] = {}
        fallback_index = 0
        for row in rows:
            visitor_id = str(row["visitor_id"])
            channel = (
                row.get("utm_medium")
                or row.get("utm_source")
                or row.get("category")
                or "unknown"
            )
            event_type = str(row["type"])
            if event_type in {"campaign_impression", "campaign_click", "campaign_open"}:
                touches_by_visitor.setdefault(visitor_id, []).append(
                    (row["occurred_at"], str(channel))
                )
            if event_type == "purchase":
                order_id = row.get("order_id")
                key = str(order_id) if order_id else f"event:{fallback_index}"
                fallback_index += 1
                purchases.setdefault(
                    key,
                    (visitor_id, row["occurred_at"], float(row.get("amount") or 0.0)),
                )

        window_seconds = attribution_window_hours * 3600
        touchpoint_counts: dict[str, int] = {}
        purchaser_sets: dict[str, set[str]] = {}
        purchase_counts: dict[str, int] = {}
        revenue: dict[str, float] = {}
        for touches in touches_by_visitor.values():
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
        return [
            AttributionChannel(
                channel=channel,
                touchpoint_count=touchpoint_counts.get(channel, 0),
                purchaser_count=len(purchaser_sets.get(channel, set())),
                purchase_count=purchase_counts.get(channel, 0),
                revenue=revenue.get(channel, 0.0),
                model="last_touch",
            )
            for channel in sorted(
                set(touchpoint_counts) | set(revenue),
                key=lambda item: revenue.get(item, 0.0),
                reverse=True,
            )
        ]

    async def save_datatalk_snapshot(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        snapshot: DataTalkSnapshot,
    ) -> None:
        if self._snapshot_session is None:
            raise ClickHouseQueryError(
                "DataTalk snapshot persistence requires a relational session; "
                "ClickHouse analytics backend was constructed without one."
            )
        # 순환 import 방지를 위해 지연 import.
        from app.analytics.adapters.repository import SqlAnalyticsRepository

        await SqlAnalyticsRepository(self._snapshot_session).save_datatalk_snapshot(
            tenant_id, start, end, snapshot
        )

    async def _order_facts(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ):
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                visitor_id,
                order_id,
                type,
                amount,
                occurred_at,
                session_id,
                utm_medium,
                utm_source,
                category
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        events = [
            OrderEvent(
                visitor_id=str(row["visitor_id"]),
                order_id=row.get("order_id"),
                type=str(row["type"]),
                amount=float(row.get("amount") or 0.0),
                occurred_at=row["occurred_at"],
                session_id=row.get("session_id"),
                utm_medium=row.get("utm_medium"),
                utm_source=row.get("utm_source"),
                category=row.get("category"),
            )
            for row in rows
        ]
        return fold_order_facts(
            tenant_id, events, attribution_window_hours=attribution_window_hours
        )

    async def refresh_order_facts(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> int:
        # raw events 를 fold 해 order_facts read-model 테이블에 머티리얼라이즈한다
        # (delete-then-insert). 조회(order_revenue_breakdown)는 이 테이블만 읽는다.
        facts = await self._order_facts(tenant_id, start, end, attribution_window_hours)
        computed_at = datetime.now(tz=timezone.utc)
        await _command(
            self._client,
            f"ALTER TABLE {self._order_facts_table} DELETE "
            "WHERE tenant_id = {tenant_id:String} "
            "AND occurred_at >= {start:DateTime} AND occurred_at < {end:DateTime}",
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        rows = [
            [
                f.tenant_id, f.order_id, f.visitor_id, f.amount, f.status, f.channel,
                f.onsite_matched, f.attributed, f.attributed_channel,
                f.occurred_at, computed_at,
            ]
            for f in facts
        ]
        await _insert(self._client, self._order_facts_table, rows, _ORDER_FACT_COLUMNS)
        return len(facts)

    async def order_revenue_breakdown(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                order_id,
                argMax(amount, computed_at) AS amount,
                argMax(status, computed_at) AS status,
                argMax(onsite_matched, computed_at) AS onsite_matched,
                argMax(attributed, computed_at) AS attributed
            FROM {self._order_facts_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            GROUP BY order_id
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        facts = [
            OrderFact(
                tenant_id=tenant_id,
                order_id=str(row.get("order_id")),
                visitor_id="",
                amount=float(row.get("amount") or 0.0),
                status=str(row.get("status") or "completed"),
                channel="",
                onsite_matched=bool(row.get("onsite_matched")),
                attributed=bool(row.get("attributed")),
                attributed_channel=None,
                occurred_at=end,
            )
            for row in rows
        ]
        total, onsite, attributed = revenue_breakdown_from_facts(facts)
        return RevenueBreakdown(
            total_revenue=total,
            onsite_revenue=onsite,
            attributed_revenue=attributed,
        )

    def _classify(
        self, inputs: list[VisitorLifecycleInput], as_of: datetime
    ) -> list[LifecycleSegment]:
        return [
            LifecycleSegment(
                visitor_id=item.visitor_id,
                visit_segment=visit_segment(as_of, item.last_seen_at),
                purchase_segment=purchase_segment(
                    as_of, item.purchase_count, item.last_purchase_at
                ),
                revenue=item.revenue,
            )
            for item in inputs
        ]

    async def refresh_lifecycle_segments(
        self, tenant_id: str, start: datetime, end: datetime, as_of: datetime
    ) -> int:
        # 기간 집계로 as_of 시점 세그먼트를 분류해 customer_segment_daily 에
        # 머티리얼라이즈한다(delete-then-insert). 조회는 이 테이블만 읽는다.
        inputs = await self.lifecycle_inputs(tenant_id, start, end)
        segments = self._classify(inputs, as_of)
        computed_at = datetime.now(tz=timezone.utc)
        await _command(
            self._client,
            f"ALTER TABLE {self._segment_table} DELETE "
            "WHERE tenant_id = {tenant_id:String} AND as_of = {as_of:DateTime}",
            {"tenant_id": tenant_id, "as_of": as_of},
        )
        rows = [
            [
                tenant_id, s.visitor_id, as_of, s.visit_segment,
                s.purchase_segment, s.revenue, computed_at,
            ]
            for s in segments
        ]
        await _insert(self._client, self._segment_table, rows, _SEGMENT_COLUMNS)
        return len(segments)

    async def segment_snapshot(
        self, tenant_id: str, as_of: datetime
    ) -> list[LifecycleSegment]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                customer_id,
                argMax(visit_segment, computed_at) AS visit_segment,
                argMax(purchase_segment, computed_at) AS purchase_segment,
                argMax(revenue, computed_at) AS revenue
            FROM {self._segment_table}
            WHERE tenant_id = {{tenant_id:String}} AND as_of = {{as_of:DateTime}}
            GROUP BY customer_id
            """,
            {"tenant_id": tenant_id, "as_of": as_of},
        )
        return [
            LifecycleSegment(
                visitor_id=str(row["customer_id"]),
                visit_segment=str(row["visit_segment"]),
                purchase_segment=str(row["purchase_segment"]),
                revenue=float(row.get("revenue") or 0.0),
            )
            for row in rows
        ]

    async def _cohort_event_times(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        cohort_type: str,
    ) -> list[tuple[str, datetime]]:
        types = cohort_event_types(cohort_type)
        type_list = ", ".join(f"'{t}'" for t in types)
        rows = await _query_rows(
            self._client,
            f"""
            SELECT visitor_id, occurred_at
            FROM {self._events_relation}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND type IN ({type_list})
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        return [(str(row["visitor_id"]), row["occurred_at"]) for row in rows]

    async def refresh_cohort_retention(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        cohort_type: str,
        granularity: str,
        max_offset: int,
    ) -> int:
        # 이벤트로 코호트 셀을 산출해 cohort_retention 테이블에 머티리얼라이즈한다
        # (delete-then-insert). 조회는 이 테이블만 읽는다.
        records = await self._cohort_event_times(tenant_id, start, end, cohort_type)
        cohort_rows = build_cohort_rows(
            records, granularity=granularity, max_offset=max_offset
        )
        computed_at = datetime.now(tz=timezone.utc)
        await _command(
            self._client,
            f"ALTER TABLE {self._cohort_table} DELETE "
            "WHERE tenant_id = {tenant_id:String} "
            "AND cohort_type = {cohort_type:String} "
            "AND granularity = {granularity:String}",
            {"tenant_id": tenant_id, "cohort_type": cohort_type, "granularity": granularity},
        )
        rows: list[list[Any]] = []
        for cohort_row in cohort_rows:
            for cell in cohort_row.cells:
                rows.append([
                    tenant_id, cohort_type, granularity, cohort_row.cohort,
                    cell.offset, cohort_row.size, cell.active, cell.rate, computed_at,
                ])
        await _insert(self._client, self._cohort_table, rows, _COHORT_COLUMNS)
        return len(rows)

    async def cohort_retention(
        self, tenant_id: str, cohort_type: str, granularity: str
    ) -> CohortReport:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT
                cohort,
                offset,
                argMax(base_count, computed_at) AS base_count,
                argMax(retained_count, computed_at) AS retained_count,
                argMax(retention_rate, computed_at) AS retention_rate
            FROM {self._cohort_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND cohort_type = {{cohort_type:String}}
              AND granularity = {{granularity:String}}
            GROUP BY cohort, offset
            ORDER BY cohort, offset
            """,
            {"tenant_id": tenant_id, "cohort_type": cohort_type, "granularity": granularity},
        )
        by_cohort: dict[str, tuple[int, list[CohortCell]]] = {}
        for row in rows:
            cohort = str(row["cohort"])
            base, cells = by_cohort.setdefault(cohort, (int(row["base_count"] or 0), []))
            cells.append(
                CohortCell(
                    offset=int(row["offset"] or 0),
                    active=int(row["retained_count"] or 0),
                    rate=float(row["retention_rate"] or 0.0),
                )
            )
        report_rows = tuple(
            CohortRow(
                cohort=cohort,
                size=size,
                cells=tuple(sorted(cells, key=lambda c: c.offset)),
            )
            for cohort, (size, cells) in by_cohort.items()
        )
        return CohortReport(rows=report_rows)


def create_clickhouse_client(dsn: str) -> ClickHouseClient:
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError(
            "ClickHouse analytics mode requires clickhouse-connect. Install "
            "clickhouse-connect or use SUNRISE_ANALYTICS_BACKEND=sql."
        ) from exc

    return clickhouse_connect.get_client(dsn=dsn)
