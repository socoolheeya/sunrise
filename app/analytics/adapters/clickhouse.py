"""ClickHouse 기반 AnalyticsRepository 구현.

운영형 분석 모드에서 raw/event read model 또는 materialized view 테이블을 조회한다.
clickhouse-connect 는 ClickHouse 모드에서만 lazy import 하므로 로컬 SQL 모드와
테스트는 외부 OLAP 인프라 없이 동작한다.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Protocol

from app.analytics.domain.model import (
    AttributionChannel,
    DataTalkSnapshot,
    InflowChannel,
    MetricInputs,
    RevenueBreakdown,
    VisitorLifecycleInput,
)
from app.analytics.domain.repository import AnalyticsRepository


class ClickHouseClient(Protocol):
    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> Any: ...


class ClickHouseQueryError(RuntimeError):
    """Raised when the ClickHouse backend is unavailable or rejects a query."""


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
    ) -> None:
        self._client = client
        self._events_table = events_table
        self._metric_daily_table = metric_daily_table

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
        _ = (tenant_id, start, end, snapshot)


def create_clickhouse_client(dsn: str) -> ClickHouseClient:
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError(
            "ClickHouse analytics mode requires clickhouse-connect. Install "
            "clickhouse-connect or use SUNRISE_ANALYTICS_BACKEND=sql."
        ) from exc

    return clickhouse_connect.get_client(dsn=dsn)
