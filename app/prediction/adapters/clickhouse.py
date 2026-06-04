"""ClickHouse feature repository for prediction production mode."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.analytics.adapters.clickhouse import ClickHouseClient, _query_rows
from app.prediction.domain.model import ProductSignal, VisitorFeatures
from app.prediction.domain.repository import PredictionRepository


class ClickHousePredictionRepository(PredictionRepository):
    def __init__(
        self,
        client: ClickHouseClient,
        events_table: str,
        visitor_features_table: str | None = None,
        visitor_product_signals_table: str | None = None,
    ) -> None:
        self._client = client
        self._events_table = events_table
        self._visitor_features_table = visitor_features_table
        self._visitor_product_signals_table = visitor_product_signals_table

    async def visitor_features(
        self, tenant_id: str, visitor_ids: list[str], start: datetime, end: datetime
    ) -> list[VisitorFeatures]:
        if not visitor_ids:
            return []

        if self._visitor_features_table is not None:
            sql = f"""
            SELECT
                visitor_id,
                sum(view_count) AS view_count,
                sum(cart_add_count) AS cart_add_count,
                sum(purchase_count) AS purchase_count,
                coalesce(sum(revenue), 0) AS revenue,
                max(last_seen_at) AS last_seen_at,
                max(last_purchase_at) AS last_purchase_at
            FROM {self._visitor_features_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id IN {{visitor_ids:Array(String)}}
              AND period >= toDate({{start:DateTime}})
              AND period < toDate({{end:DateTime}})
            GROUP BY visitor_id
            """
        else:
            sql = f"""
            SELECT
                visitor_id,
                countIf(type = 'view') AS view_count,
                countIf(type = 'cart_add') AS cart_add_count,
                countIf(type = 'purchase') AS purchase_count,
                coalesce(sumIf(amount, type = 'purchase'), 0) AS revenue,
                max(occurred_at) AS last_seen_at,
                maxIf(occurred_at, type = 'purchase') AS last_purchase_at
            FROM {self._events_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id IN {{visitor_ids:Array(String)}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
            GROUP BY visitor_id
            """
        rows = await _query_rows(
            self._client,
            sql,
            {
                "tenant_id": tenant_id,
                "visitor_ids": visitor_ids,
                "start": start,
                "end": end,
            },
        )
        by_visitor = {
            str(row["visitor_id"]): VisitorFeatures(
                visitor_id=str(row["visitor_id"]),
                view_count=int(row.get("view_count") or 0),
                cart_add_count=int(row.get("cart_add_count") or 0),
                purchase_count=int(row.get("purchase_count") or 0),
                revenue=float(row.get("revenue") or 0.0),
                last_seen_at=_empty_epoch_to_none(row.get("last_seen_at")),
                last_purchase_at=_empty_epoch_to_none(row.get("last_purchase_at")),
            )
            for row in rows
        }
        return [
            by_visitor.get(
                visitor_id,
                VisitorFeatures(
                    visitor_id=visitor_id,
                    view_count=0,
                    cart_add_count=0,
                    purchase_count=0,
                    revenue=0.0,
                    last_seen_at=None,
                    last_purchase_at=None,
                ),
            )
            for visitor_id in visitor_ids
        ]

    async def product_signals(
        self,
        tenant_id: str,
        visitor_id: str,
        keys: list[str] | None,
        start: datetime,
        end: datetime,
    ) -> list[ProductSignal]:
        key_filter = "AND key IN {keys:Array(String)}" if keys else ""
        if self._visitor_product_signals_table is not None:
            sql = f"""
            SELECT
                key,
                sum(view_count) AS view_count,
                sum(cart_add_count) AS cart_add_count,
                sum(purchase_count) AS purchase_count
            FROM {self._visitor_product_signals_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND period >= toDate({{start:DateTime}})
              AND period < toDate({{end:DateTime}})
              {key_filter}
            GROUP BY key
            """
        else:
            raw_key_filter = (
                "AND coalesce(product_id, category) IN {keys:Array(String)}"
                if keys
                else ""
            )
            sql = f"""
            SELECT
                coalesce(product_id, category) AS key,
                countIf(type = 'view') AS view_count,
                countIf(type = 'cart_add') AS cart_add_count,
                countIf(type = 'purchase') AS purchase_count
            FROM {self._events_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND coalesce(product_id, category) IS NOT NULL
              {raw_key_filter}
            GROUP BY key
            """
        rows = await _query_rows(
            self._client,
            sql,
            {
                "tenant_id": tenant_id,
                "visitor_id": visitor_id,
                "keys": keys or [],
                "start": start,
                "end": end,
            },
        )
        return [
            ProductSignal(
                key=str(row["key"]),
                view_count=int(row.get("view_count") or 0),
                cart_add_count=int(row.get("cart_add_count") or 0),
                purchase_count=int(row.get("purchase_count") or 0),
            )
            for row in rows
        ]


def _empty_epoch_to_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.year <= 1970:
        return None
    return value
