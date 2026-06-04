"""ClickHouse 기반 AnalyticsRepository 구현.

운영형 분석 모드에서 raw/event read model 또는 materialized view 테이블을 조회한다.
clickhouse-connect 는 ClickHouse 모드에서만 lazy import 하므로 로컬 SQL 모드와
테스트는 외부 OLAP 인프라 없이 동작한다.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Protocol

from app.analytics.domain.model import MetricInputs
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

    async def metric_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> MetricInputs:
        if self._metric_daily_table is not None:
            return await self._metric_inputs_from_daily(
                "tenant_id = {tenant_id:String}",
                {"tenant_id": tenant_id, "start": start, "end": end},
            )
        return await self._metric_inputs(
            "tenant_id = {tenant_id:String}",
            {"tenant_id": tenant_id, "start": start, "end": end},
        )

    async def platform_metric_inputs(
        self, start: datetime, end: datetime
    ) -> MetricInputs:
        if self._metric_daily_table is not None:
            return await self._metric_inputs_from_daily(
                "1 = 1", {"start": start, "end": end}
            )
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
                FROM {self._events_table}
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
            SELECT
                uniqExact(visitor_id) AS visitor_count,
                uniqExactIf(visitor_id, type = 'purchase') AS purchaser_count,
                countIf(type = 'purchase') AS purchase_count,
                coalesce(sumIf(amount, type = 'purchase'), 0) AS revenue
            FROM {self._events_table}
            WHERE {base_filter}
            """,
            params,
        )
        repeat_rows = await _query_rows(
            self._client,
            f"""
            SELECT count() AS repeat_purchaser_count
            FROM (
                SELECT visitor_id
                FROM {self._events_table}
                WHERE {base_filter} AND type = 'purchase'
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
            repeat_purchaser_count=int(
                repeat_row.get("repeat_purchaser_count") or 0
            ),
        )

    async def funnel_visitor_counts(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> dict[str, int]:
        rows = await _query_rows(
            self._client,
            f"""
            SELECT type, uniqExact(visitor_id) AS visitors
            FROM {self._events_table}
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
            FROM {self._events_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND type = 'purchase'
            """,
            {"tenant_id": tenant_id, "start": start, "end": end},
        )
        return [(str(row["visitor_id"]), str(row["period"])) for row in rows]


def create_clickhouse_client(dsn: str) -> ClickHouseClient:
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError(
            "ClickHouse analytics mode requires clickhouse-connect. Install "
            "clickhouse-connect or use SUNRISE_ANALYTICS_BACKEND=sql."
        ) from exc

    return clickhouse_connect.get_client(dsn=dsn)
