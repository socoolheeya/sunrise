"""ClickHouse 분석 백엔드 테스트.

외부 ClickHouse 없이 FakeClient 로 AnalyticsRepository 계약과 HTTP 와이어링을 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.analytics.adapters.clickhouse import (
    ClickHouseAnalyticsRepository,
    ClickHouseQueryError,
)

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 3, tzinfo=timezone.utc)


class FakeClickHouseClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.closed = False

    def query(self, sql: str, parameters=None):
        params = parameters or {}
        self.queries.append((sql, params))

        if "SELECT visitor_id, session_id, order_id, type, amount" in sql:
            return [
                {"visitor_id": "v1", "session_id": "s1", "order_id": None, "type": "view", "amount": None},
                {"visitor_id": "v1", "session_id": "s1", "order_id": "o1", "type": "purchase", "amount": 100.0},
                {"visitor_id": "v1", "session_id": "s1", "order_id": "o1", "type": "purchase", "amount": 100.0},
                {"visitor_id": "v2", "session_id": "s2", "order_id": "o2", "type": "purchase", "amount": 200.0},
                {"visitor_id": "v3", "session_id": "s3", "order_id": None, "type": "view", "amount": None},
                {"visitor_id": "v4", "session_id": "s4", "order_id": None, "type": "cart_add", "amount": None},
            ]
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
    assert result.purchase_count == 2
    assert result.revenue == 300.0
    assert result.repeat_purchaser_count == 0
    assert result.session_count == 4
    assert len(client.queries) == 1
    assert "FROM events_mv" in client.queries[0][0]
    assert client.queries[0][1]["tenant_id"] == "tenant-a"


async def test_clickhouse_repository_prefers_raw_metrics_for_order_deduplication():
    client = FakeClickHouseClient()
    repo = ClickHouseAnalyticsRepository(
        client,
        "events",
        metric_daily_table="agg_metric_daily",
    )

    result = await repo.metric_inputs("tenant-a", START, END)

    assert result.visitor_count == 4
    assert result.purchase_count == 2
    assert result.revenue == 300.0
    assert result.session_count == 4
    assert len(client.queries) == 1
    assert "FROM events" in client.queries[0][0]
    assert client.queries[0][1]["tenant_id"] == "tenant-a"


async def test_clickhouse_repository_platform_metrics_use_raw_events():
    client = FakeClickHouseClient()
    repo = ClickHouseAnalyticsRepository(
        client,
        "events",
        metric_daily_table="agg_metric_daily",
    )

    result = await repo.platform_metric_inputs(START, END)

    assert result.visitor_count == 4
    assert result.session_count == 4
    assert len(client.queries) == 1
    assert "FROM events" in client.queries[0][0]
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
    assert body["visitor_count"] == 4
    assert body["session_count"] == 4
    assert body["cvr"] == 0.5
    assert "FROM events_mv" in fake.queries[0][0]


def _datatalk_snapshot():
    from app.analytics.domain.model import (
        DashboardMetrics,
        DataTalkReport,
        DataTalkSnapshot,
        Funnel,
        RevenueBreakdown,
    )

    report = DataTalkReport(
        metrics=DashboardMetrics(
            revenue=150.0,
            session_count=2,
            visitor_count=2,
            purchase_count=2,
            cvr=0.5,
            aov=75.0,
            repeat_rate=0.0,
        ),
        funnel=Funnel(steps=()),
        revenue_breakdown=RevenueBreakdown(
            total_revenue=150.0, onsite_revenue=100.0, attributed_revenue=100.0
        ),
        top_inflow_channels=(),
        anomalies=(),
    )
    return DataTalkSnapshot(
        snapshot_id="snap-1",
        status="frozen",
        report=report,
        generated_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )


async def test_clickhouse_repository_persists_datatalk_snapshot_to_sql(tmp_path):
    """ClickHouse 모드에서도 snapshot 이 관계형 저장소에 실제 저장된다 (no-op 회귀 방지)."""
    from app.core import config, database
    from app.core.database import get_sessionmaker, init_models
    from app.core.orm import DataTalkSnapshotRow

    config._settings = None
    database.reset_state()
    await init_models()

    snapshot = _datatalk_snapshot()
    async with get_sessionmaker()() as session:
        repo = ClickHouseAnalyticsRepository(
            FakeClickHouseClient(), "events", snapshot_session=session
        )
        await repo.save_datatalk_snapshot("tenant-a", START, END, snapshot)

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(DataTalkSnapshotRow).where(
                    DataTalkSnapshotRow.tenant_id == "tenant-a",
                    DataTalkSnapshotRow.snapshot_id == "snap-1",
                )
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].status == "frozen"
    assert '"revenue": 150.0' in rows[0].payload_json
    await database.close_database()


async def test_clickhouse_repository_snapshot_without_session_raises():
    """세션이 없으면 조용히 버리지 않고 실패시킨다 (silent data loss 방지)."""
    repo = ClickHouseAnalyticsRepository(FakeClickHouseClient(), "events")
    with pytest.raises(ClickHouseQueryError):
        await repo.save_datatalk_snapshot("tenant-a", START, END, _datatalk_snapshot())


def _dt(day: int, hour: int = 0, minute: int = 0):
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc)


class RecordingClickHouseClient:
    """refresh insert → read SELECT round-trip 을 시뮬레이트하는 fake.

    - read-model 테이블(order_facts/customer_segment_daily/cohort_retention)을
      FROM 하는 query 는 insert 로 저장된 행을 반환한다.
    - command(ALTER … DELETE) 는 해당 테이블 저장분을 비운다(delete-then-insert).
    - fold 입력 쿼리는 생성자에 주어진 event/lifecycle row 를 반환한다.
    """

    _READ_TABLES = ("order_facts", "customer_segment_daily", "cohort_retention")

    def __init__(self, *, order_events=None, cohort_events=None, lifecycle_rows=None):
        self._order_events = order_events or []
        self._cohort_events = cohort_events or []
        self._lifecycle_rows = lifecycle_rows or []
        self.tables: dict[str, list[dict]] = {}
        self.inserts: list[str] = []
        self.commands: list[str] = []

    def query(self, sql, parameters=None):
        for table in self._READ_TABLES:
            if f"FROM {table}" in sql:
                return list(self.tables.get(table, []))
        if "countIf" in sql:
            return list(self._lifecycle_rows)
        if "type IN (" in sql:
            return list(self._cohort_events)
        if "session_id," in sql:
            return list(self._order_events)
        return []

    def command(self, sql, parameters=None):
        self.commands.append(sql)
        for table in self._READ_TABLES:
            if f"ALTER TABLE {table} DELETE" in sql:
                self.tables[table] = []

    def insert(self, table, data, column_names=None):
        self.inserts.append(table)
        stored = self.tables.setdefault(table, [])
        for row in data:
            stored.append(dict(zip(column_names, row)))


async def test_clickhouse_order_facts_refresh_then_read_from_table():
    """CH order_fact: refresh 가 order_facts 테이블에 적재하고 조회는 그 테이블만 읽는다."""
    order_events = [
        {"visitor_id": "v2", "order_id": None, "type": "campaign_click",
         "amount": None, "occurred_at": _dt(1, 9), "session_id": None,
         "utm_medium": "kakao", "utm_source": None, "category": None},
        {"visitor_id": "v1", "order_id": "o1", "type": "purchase", "amount": 100.0,
         "occurred_at": _dt(1, 10), "session_id": "s1", "utm_medium": None,
         "utm_source": None, "category": None},
        # 같은 주문 o1 중복 purchase 이벤트
        {"visitor_id": "v1", "order_id": "o1", "type": "purchase", "amount": 100.0,
         "occurred_at": _dt(1, 10, 1), "session_id": "s1", "utm_medium": None,
         "utm_source": None, "category": None},
        {"visitor_id": "v2", "order_id": "o2", "type": "purchase", "amount": 200.0,
         "occurred_at": _dt(1, 9, 30), "session_id": "s2", "utm_medium": "kakao",
         "utm_source": None, "category": None},
        {"visitor_id": "v3", "order_id": "o3", "type": "purchase", "amount": 50.0,
         "occurred_at": _dt(1, 11), "session_id": None, "utm_medium": None,
         "utm_source": None, "category": None},
    ]
    client = RecordingClickHouseClient(order_events=order_events)
    repo = ClickHouseAnalyticsRepository(client, "events")

    count = await repo.refresh_order_facts("tenant-a", START, END, 24)
    breakdown = await repo.order_revenue_breakdown("tenant-a", START, END)

    assert count == 3  # o1, o2, o3 (중복 dedup)
    assert client.inserts == ["order_facts"]  # 실제 테이블 적재
    assert breakdown.total_revenue == 350.0
    assert breakdown.onsite_revenue == 300.0  # o1 + o2
    assert breakdown.hidden_revenue == 50.0  # o3
    assert breakdown.attributed_revenue == 200.0  # o2


async def test_clickhouse_order_facts_refresh_is_idempotent():
    order_events = [
        {"visitor_id": "v1", "order_id": "o1", "type": "purchase", "amount": 100.0,
         "occurred_at": _dt(1, 10), "session_id": "s1", "utm_medium": None,
         "utm_source": None, "category": None},
    ]
    client = RecordingClickHouseClient(order_events=order_events)
    repo = ClickHouseAnalyticsRepository(client, "events")

    await repo.refresh_order_facts("tenant-a", START, END, 24)
    await repo.refresh_order_facts("tenant-a", START, END, 24)
    breakdown = await repo.order_revenue_breakdown("tenant-a", START, END)

    # delete-then-insert → 두 번 refresh 해도 단일 주문
    assert breakdown.total_revenue == 100.0
    assert client.tables["order_facts"] and len(client.tables["order_facts"]) == 1


async def test_clickhouse_segment_refresh_then_snapshot_from_table():
    lifecycle_rows = [
        {"visitor_id": "v1", "view_count": 3, "purchase_count": 0, "revenue": 0.0,
         "last_seen_at": _dt(2), "last_purchase_at": None},
        {"visitor_id": "v2", "view_count": 5, "purchase_count": 1, "revenue": 100.0,
         "last_seen_at": _dt(1), "last_purchase_at": _dt(1)},
    ]
    client = RecordingClickHouseClient(lifecycle_rows=lifecycle_rows)
    repo = ClickHouseAnalyticsRepository(client, "events")
    as_of = END

    count = await repo.refresh_lifecycle_segments("tenant-a", START, END, as_of)
    snapshot = await repo.segment_snapshot("tenant-a", as_of)

    assert count == 2
    assert client.inserts == ["customer_segment_daily"]
    by_id = {s.visitor_id: s for s in snapshot}
    assert by_id["v1"].visit_segment == "visit_active"
    assert by_id["v1"].purchase_segment == "no_purchase"
    assert by_id["v2"].purchase_segment == "purchase_active"


async def test_clickhouse_cohort_refresh_then_read_from_table():
    cohort_events = [
        {"visitor_id": "v1", "occurred_at": _dt(1)},
        {"visitor_id": "v1", "occurred_at": _dt(2)},
        {"visitor_id": "v2", "occurred_at": _dt(1)},
    ]
    client = RecordingClickHouseClient(cohort_events=cohort_events)
    repo = ClickHouseAnalyticsRepository(client, "events")

    cells = await repo.refresh_cohort_retention("tenant-a", START, END, "purchase", "day", 11)
    report = await repo.cohort_retention("tenant-a", "purchase", "day")

    assert cells == 2  # offset 0, 1
    assert client.inserts == ["cohort_retention"]
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.cohort == "2026-06-01"
    assert row.size == 2
    by_offset = {c.offset: c for c in row.cells}
    assert by_offset[0].rate == 1.0
    assert by_offset[1].active == 1 and by_offset[1].rate == 0.5


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
