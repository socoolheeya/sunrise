"""ClickHouse feature repository for recommendation production mode."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from app.analytics.adapters.clickhouse import (
    ClickHouseClient,
    ClickHouseQueryError,
    _query_rows,
)
from app.recommendation.domain.model import ProductStat, VisitorContext
from app.recommendation.domain.repository import RecommendationRepository


class ClickHouseRecommendationRepository(RecommendationRepository):
    def __init__(
        self,
        client: ClickHouseClient,
        events_table: str,
        product_stats_table: str | None = None,
        product_features_table: str | None = None,
        visitor_product_signals_table: str | None = None,
    ) -> None:
        self._client = client
        self._events_table = events_table
        self._product_stats_table = product_stats_table
        self._product_features_table = product_features_table
        self._visitor_product_signals_table = visitor_product_signals_table

    async def popular_products(
        self, tenant_id: str, start: datetime, end: datetime, limit: int
    ) -> list[ProductStat]:
        if self._product_stats_table is not None:
            candidate_sql = f"""
                SELECT
                    product_id,
                    anyLastMerge(category_state) AS event_category,
                    sum(view_count) AS view_count,
                    sum(cart_add_count) AS cart_add_count,
                    sum(purchase_count) AS purchase_count,
                    uniqMerge(buyer_state) AS buyer_count,
                    purchase_count * 3 + cart_add_count * 2 + view_count AS weight
                FROM {self._product_stats_table}
                WHERE tenant_id = {{tenant_id:String}}
                  AND period >= toDate({{start:DateTime}})
                  AND period < toDate({{end:DateTime}})
                GROUP BY product_id
            """
        else:
            candidate_sql = f"""
                SELECT
                    product_id,
                    anyLast(category) AS event_category,
                    countIf(type = 'view') AS view_count,
                    countIf(type = 'cart_add') AS cart_add_count,
                    countIf(type = 'purchase') AS purchase_count,
                    uniqExactIf(visitor_id, type = 'purchase') AS buyer_count,
                    purchase_count * 3 + cart_add_count * 2 + view_count AS weight
                FROM {self._events_table}
                WHERE tenant_id = {{tenant_id:String}}
                  AND occurred_at >= {{start:DateTime}}
                  AND occurred_at < {{end:DateTime}}
                  AND product_id IS NOT NULL
                GROUP BY product_id
            """
        sql = self._popular_products_sql(candidate_sql)
        rows = await _query_rows(
            self._client,
            sql,
            {"tenant_id": tenant_id, "start": start, "end": end, "limit": limit},
        )
        return [
            ProductStat(
                product_id=str(row["product_id"]),
                category=row.get("category"),
                view_count=int(row.get("view_count") or 0),
                cart_add_count=int(row.get("cart_add_count") or 0),
                purchase_count=int(row.get("purchase_count") or 0),
                buyer_count=int(row.get("buyer_count") or 0),
                price=_float_or_none(row.get("price")),
                original_price=_float_or_none(row.get("original_price")),
                gross_margin=_float_or_none(row.get("gross_margin")),
                rating=_float_or_none(row.get("rating")),
                review_count=(
                    int(row["review_count"]) if row.get("review_count") is not None else None
                ),
                return_rate=_float_or_none(row.get("return_rate")),
                category_avg_price=_float_or_none(row.get("category_avg_price")),
                in_stock=bool(row.get("in_stock", True)),
            )
            for row in rows
        ]

    def _popular_products_sql(self, candidate_sql: str) -> str:
        if self._product_features_table is None:
            return f"""
            WITH candidate AS ({candidate_sql})
            SELECT
                product_id,
                event_category AS category,
                view_count,
                cart_add_count,
                purchase_count,
                buyer_count
            FROM candidate
            ORDER BY weight DESC, product_id
            LIMIT {{limit:UInt32}}
            """

        product_table = self._product_features_table
        latest_features_sql = f"""
            SELECT
                product_id,
                argMax(category, updated_at) AS feature_category,
                argMax(price, updated_at) AS price,
                argMax(original_price, updated_at) AS original_price,
                argMax(gross_margin, updated_at) AS gross_margin,
                argMax(rating, updated_at) AS rating,
                argMax(review_count, updated_at) AS review_count,
                argMax(return_rate, updated_at) AS return_rate,
                argMax(in_stock, updated_at) AS in_stock
            FROM {product_table}
            WHERE tenant_id = {{tenant_id:String}}
            GROUP BY product_id
        """
        return f"""
        WITH
            candidate AS ({candidate_sql}),
            latest_product_features AS ({latest_features_sql}),
            category_prices AS (
                SELECT
                    feature_category AS category,
                    avg(price) AS category_avg_price
                FROM latest_product_features
                WHERE feature_category IS NOT NULL
                  AND price IS NOT NULL
                GROUP BY feature_category
            )
        SELECT
            c.product_id AS product_id,
            coalesce(p.feature_category, c.event_category) AS category,
            c.view_count AS view_count,
            c.cart_add_count AS cart_add_count,
            c.purchase_count AS purchase_count,
            c.buyer_count AS buyer_count,
            p.price,
            p.original_price,
            p.gross_margin,
            p.rating,
            p.review_count,
            p.return_rate,
            if(p.product_id = '', true, p.in_stock) AS in_stock,
            cp.category_avg_price
        FROM candidate c
        LEFT JOIN latest_product_features p ON p.product_id = c.product_id
        LEFT JOIN category_prices cp
            ON cp.category = coalesce(p.feature_category, c.event_category)
        ORDER BY c.weight DESC, c.product_id
        LIMIT {{limit:UInt32}}
        """

    async def visitor_context(
        self, tenant_id: str, visitor_id: str, start: datetime, end: datetime
    ) -> VisitorContext:
        params = {
            "tenant_id": tenant_id,
            "visitor_id": visitor_id,
            "start": start,
            "end": end,
        }
        if self._visitor_product_signals_table is not None:
            table = self._visitor_product_signals_table
            viewed_sql = f"""
            SELECT DISTINCT product_id
            FROM {table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND period >= toDate({{start:DateTime}})
              AND period < toDate({{end:DateTime}})
              AND view_count > 0
              AND product_id IS NOT NULL
            """
            purchased_sql = f"""
            SELECT DISTINCT product_id
            FROM {table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND period >= toDate({{start:DateTime}})
              AND period < toDate({{end:DateTime}})
              AND purchase_count > 0
              AND product_id IS NOT NULL
            """
            categories_sql = f"""
            SELECT DISTINCT category
            FROM {table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND period >= toDate({{start:DateTime}})
              AND period < toDate({{end:DateTime}})
              AND category IS NOT NULL
            """
        else:
            viewed_sql = f"""
            SELECT DISTINCT product_id
            FROM {self._events_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND type = 'view'
              AND product_id IS NOT NULL
            """
            purchased_sql = f"""
            SELECT DISTINCT product_id
            FROM {self._events_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND type = 'purchase'
              AND product_id IS NOT NULL
            """
            categories_sql = f"""
            SELECT DISTINCT category
            FROM {self._events_table}
            WHERE tenant_id = {{tenant_id:String}}
              AND visitor_id = {{visitor_id:String}}
              AND occurred_at >= {{start:DateTime}}
              AND occurred_at < {{end:DateTime}}
              AND category IS NOT NULL
            """
        viewed = await _query_rows(self._client, viewed_sql, params)
        purchased = await _query_rows(self._client, purchased_sql, params)
        categories = await _query_rows(self._client, categories_sql, params)
        return VisitorContext(
            visitor_id=visitor_id,
            viewed_product_ids=frozenset(str(row["product_id"]) for row in viewed),
            purchased_product_ids=frozenset(str(row["product_id"]) for row in purchased),
            engaged_categories=frozenset(str(row["category"]) for row in categories),
        )

    async def upsert_product_features(self, tenant_id: str, products: list[dict]) -> int:
        if self._product_features_table is None:
            raise ClickHouseQueryError("ClickHouse product feature table is not configured")

        now = datetime.now(timezone.utc)
        rows = [
            (
                tenant_id,
                product["product_id"],
                product.get("category"),
                product.get("price"),
                product.get("original_price"),
                product.get("gross_margin"),
                product.get("rating"),
                product.get("review_count"),
                product.get("return_rate"),
                product.get("in_stock", True),
                now,
            )
            for product in products
        ]
        try:
            insert = getattr(self._client, "insert")
            result = insert(
                self._product_features_table,
                rows,
                column_names=[
                    "tenant_id",
                    "product_id",
                    "category",
                    "price",
                    "original_price",
                    "gross_margin",
                    "rating",
                    "review_count",
                    "return_rate",
                    "in_stock",
                    "updated_at",
                ],
            )
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            raise ClickHouseQueryError("ClickHouse product feature insert failed") from exc
        return len(rows)


def _float_or_none(value: Any) -> float | None:
    return float(value) if value is not None else None
