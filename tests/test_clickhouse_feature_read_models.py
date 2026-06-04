"""ClickHouse read model repository tests for ML feature paths."""

from __future__ import annotations

from datetime import datetime, timezone

from app.prediction.adapters.clickhouse import ClickHousePredictionRepository
from app.recommendation.adapters.clickhouse import ClickHouseRecommendationRepository

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 3, tzinfo=timezone.utc)


class FakeFeatureClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.inserts: list[tuple[str, list[tuple], list[str]]] = []

    def query(self, sql: str, parameters=None):
        params = parameters or {}
        self.queries.append((sql, params))

        if "FROM visitor_features_daily" in sql:
            return [
                {
                    "visitor_id": "v1",
                    "view_count": 3,
                    "cart_add_count": 1,
                    "purchase_count": 1,
                    "revenue": 120.0,
                    "last_seen_at": END,
                    "last_purchase_at": END,
                }
            ]
        if "FROM visitor_product_signals_daily" in sql and "GROUP BY key" in sql:
            return [
                {
                    "key": "p1",
                    "view_count": 2,
                    "cart_add_count": 1,
                    "purchase_count": 1,
                }
            ]
        if "FROM product_stats_daily" in sql:
            return [
                {
                    "product_id": "p1",
                    "category": "tops",
                    "view_count": 4,
                    "cart_add_count": 2,
                    "purchase_count": 1,
                    "buyer_count": 1,
                }
            ]
        if "view_count > 0" in sql:
            return [{"product_id": "p1"}]
        if "purchase_count > 0" in sql:
            return [{"product_id": "p2"}]
        if "category IS NOT NULL" in sql:
            return [{"category": "tops"}]
        return []

    def insert(self, table: str, rows: list[tuple], column_names: list[str]):
        self.inserts.append((table, rows, column_names))


async def test_prediction_repository_uses_clickhouse_feature_read_models():
    client = FakeFeatureClient()
    repo = ClickHousePredictionRepository(
        client,
        "events",
        visitor_features_table="visitor_features_daily",
        visitor_product_signals_table="visitor_product_signals_daily",
    )

    visitors = await repo.visitor_features("tenant-a", ["v1", "unknown"], START, END)
    signals = await repo.product_signals("tenant-a", "v1", ["p1"], START, END)

    assert visitors[0].visitor_id == "v1"
    assert visitors[0].purchase_count == 1
    assert visitors[1].visitor_id == "unknown"
    assert visitors[1].purchase_count == 0
    assert signals[0].key == "p1"
    assert "FROM visitor_features_daily" in client.queries[0][0]
    assert "FROM visitor_product_signals_daily" in client.queries[1][0]


async def test_recommendation_repository_uses_clickhouse_feature_read_models():
    client = FakeFeatureClient()
    repo = ClickHouseRecommendationRepository(
        client,
        "events",
        product_stats_table="product_stats_daily",
        product_features_table="product_features",
        visitor_product_signals_table="visitor_product_signals_daily",
    )

    products = await repo.popular_products("tenant-a", START, END, 10)
    context = await repo.visitor_context("tenant-a", "v1", START, END)

    assert products[0].product_id == "p1"
    assert products[0].buyer_count == 1
    assert context.viewed_product_ids == frozenset({"p1"})
    assert context.purchased_product_ids == frozenset({"p2"})
    assert context.engaged_categories == frozenset({"tops"})
    assert "FROM product_stats_daily" in client.queries[0][0]
    assert "latest_product_features" in client.queries[0][0]
    assert "FROM product_features" in client.queries[0][0]
    assert "if(p.product_id = '', true, p.in_stock) AS in_stock" in client.queries[0][0]
    assert "FROM visitor_product_signals_daily" in client.queries[1][0]


async def test_recommendation_repository_upserts_clickhouse_product_features():
    client = FakeFeatureClient()
    repo = ClickHouseRecommendationRepository(
        client,
        "events",
        product_features_table="product_features",
    )

    accepted = await repo.upsert_product_features(
        "tenant-a",
        [
            {
                "product_id": "p1",
                "category": "tops",
                "price": 80.0,
                "original_price": 100.0,
                "gross_margin": 0.35,
                "rating": 4.7,
                "review_count": 120,
                "return_rate": 0.04,
                "in_stock": True,
            }
        ],
    )

    assert accepted == 1
    table, rows, columns = client.inserts[0]
    assert table == "product_features"
    assert rows[0][0] == "tenant-a"
    assert rows[0][1] == "p1"
    assert "updated_at" in columns
