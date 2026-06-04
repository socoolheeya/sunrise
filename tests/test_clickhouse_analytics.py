"""ClickHouse 분석 백엔드 테스트.

외부 ClickHouse 없이 FakeClient 로 AnalyticsRepository 계약과 HTTP 와이어링을 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient

from app.analytics.adapters.clickhouse import ClickHouseAnalyticsRepository

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 3, tzinfo=timezone.utc)


class FakeClickHouseClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.closed = False

    def query(self, sql: str, parameters=None):
        params = parameters or {}
        self.queries.append((sql, params))

        if "FROM agg_metric_daily" in sql:
            return [{
                "visitor_count": 10,
                "purchaser_count": 4,
                "purchase_count": 5,
                "revenue": 1000.0,
                "repeat_purchaser_count": 2,
            }]
        if "repeat_purchaser_count" in sql:
            return [{"repeat_purchaser_count": 1}]
        if "uniqExactIf" in sql:
            return [{
                "visitor_count": 4,
                "purchaser_count": 2,
                "purchase_count": 3,
                "revenue": 600.0,
            }]
        if "GROUP BY type" in sql:
            return [
                {"type": "view", "visitors": 4},
                {"type": "cart_add", "visitors": 3},
                {"type": "purchase", "visitors": 2},
            ]
        if "formatDateTime" in sql:
            return [
                {"visitor_id": "v1", "period": "2026-06"},
                {"visitor_id": "v1", "period": "2026-07"},
            ]
        return []

    def close(self):
        self.closed = True


class FailingClickHouseClient:
    def query(self, sql: str, parameters=None):
        raise RuntimeError("authentication failed")


async def test_clickhouse_repository_metric_inputs():
    client = FakeClickHouseClient()
    repo = ClickHouseAnalyticsRepository(client, "events_mv")

    result = await repo.metric_inputs("tenant-a", START, END)

    assert result.visitor_count == 4
    assert result.purchase_count == 3
    assert result.revenue == 600.0
    assert result.repeat_purchaser_count == 1
    assert len(client.queries) == 2
    assert "FROM events_mv" in client.queries[0][0]
    assert client.queries[0][1]["tenant_id"] == "tenant-a"


async def test_clickhouse_repository_uses_daily_metric_read_model_when_configured():
    client = FakeClickHouseClient()
    repo = ClickHouseAnalyticsRepository(
        client,
        "events",
        metric_daily_table="agg_metric_daily",
    )

    result = await repo.metric_inputs("tenant-a", START, END)

    assert result.visitor_count == 10
    assert result.purchase_count == 5
    assert result.revenue == 1000.0
    assert result.repeat_purchaser_count == 1
    assert len(client.queries) == 2
    assert "FROM agg_metric_daily" in client.queries[0][0]
    assert "uniqExact" not in client.queries[0][0]
    assert client.queries[0][1]["tenant_id"] == "tenant-a"


async def test_clickhouse_repository_platform_metrics_use_daily_read_model():
    client = FakeClickHouseClient()
    repo = ClickHouseAnalyticsRepository(
        client,
        "events",
        metric_daily_table="agg_metric_daily",
    )

    result = await repo.platform_metric_inputs(START, END)

    assert result.visitor_count == 10
    assert len(client.queries) == 2
    assert "FROM agg_metric_daily" in client.queries[0][0]
    assert "tenant_id = {tenant_id:String}" not in client.queries[0][0]


async def test_clickhouse_repository_funnel_and_cohort():
    client = FakeClickHouseClient()
    repo = ClickHouseAnalyticsRepository(client, "events")

    funnel = await repo.funnel_visitor_counts("tenant-a", START, END)
    purchases = await repo.purchase_months("tenant-a", START, END)

    assert funnel == {"view": 4, "cart_add": 3, "purchase": 2}
    assert purchases == [("v1", "2026-06"), ("v1", "2026-07")]


async def test_analytics_uses_clickhouse_backend_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("SUNRISE_API_KEYS", '{"test-key": "tenant-a"}')
    monkeypatch.setenv("SUNRISE_ANALYTICS_BACKEND", "clickhouse")
    monkeypatch.setenv("SUNRISE_CLICKHOUSE_EVENTS_TABLE", "events_mv")
    monkeypatch.setenv("SUNRISE_CLICKHOUSE_METRIC_DAILY_TABLE", "agg_metric_daily")

    from app.core import cache, config, database, observability
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    await init_models()

    app = create_app()
    fake = FakeClickHouseClient()
    app.state.clickhouse_client = fake

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.get(
            "/v1/analytics/metrics",
            params={
                "start": "2026-06-01T00:00:00Z",
                "end": "2026-06-03T00:00:00Z",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["visitor_count"] == 10
    assert body["cvr"] == 0.4
    assert "FROM agg_metric_daily" in fake.queries[0][0]


async def test_clickhouse_backend_requires_ready_client(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("SUNRISE_API_KEYS", '{"test-key": "tenant-a"}')
    monkeypatch.setenv("SUNRISE_ANALYTICS_BACKEND", "clickhouse")

    from app.core import cache, config, database, observability
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    await init_models()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.get("/v1/analytics/metrics")

    assert response.status_code == 503
    assert response.json()["detail"] == "ClickHouse client is not ready"


async def test_clickhouse_backend_query_failure_returns_service_unavailable(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("SUNRISE_API_KEYS", '{"test-key": "tenant-a"}')
    monkeypatch.setenv("SUNRISE_ANALYTICS_BACKEND", "clickhouse")
    monkeypatch.setenv("SUNRISE_CLICKHOUSE_EVENTS_TABLE", "events_mv")

    from app.core import cache, config, database, observability
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    await init_models()

    app = create_app()
    app.state.clickhouse_client = FailingClickHouseClient()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.get(
            "/v1/analytics/metrics",
            params={
                "start": "2026-06-01T00:00:00Z",
                "end": "2026-06-03T00:00:00Z",
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "ClickHouse backend is unavailable"
