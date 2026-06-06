"""코호트/벤치마크 도메인 + API, 캐시 동작 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.analytics.domain.model import (
    BenchmarkReport,
    CohortReport,
    DashboardMetrics,
    MetricInputs,
)


# ---- 코호트 도메인 ----
def test_cohort_retention_matrix():
    # v1: 2026-01, 2026-02 (offset 0,1) / v2: 2026-01 (offset 0) / v3: 2026-02 (offset 0)
    records = [
        ("v1", "2026-01"),
        ("v1", "2026-02"),
        ("v2", "2026-01"),
        ("v3", "2026-02"),
    ]
    report = CohortReport.from_purchases(records)
    by_cohort = {r.cohort: r for r in report.rows}

    jan = by_cohort["2026-01"]
    assert jan.size == 2  # v1, v2
    assert jan.cells[0].active == 2 and jan.cells[0].rate == pytest.approx(1.0)
    assert jan.cells[1].active == 1 and jan.cells[1].rate == pytest.approx(0.5)

    feb = by_cohort["2026-02"]
    assert feb.size == 1  # v3
    assert feb.cells[0].active == 1


def test_cohort_empty():
    assert CohortReport.from_purchases([]).rows == ()


# ---- 벤치마크 도메인 ----
def test_benchmark_compare_delta():
    tenant = DashboardMetrics.from_inputs(
        MetricInputs(visitor_count=10, purchaser_count=3, purchase_count=3,
                     revenue=300.0, repeat_purchaser_count=0)
    )  # cvr 0.3, aov 100
    platform = DashboardMetrics.from_inputs(
        MetricInputs(visitor_count=10, purchaser_count=2, purchase_count=2,
                     revenue=400.0, repeat_purchaser_count=0)
    )  # cvr 0.2, aov 200
    report = BenchmarkReport.compare(tenant, platform)
    by_name = {m.name: m for m in report.metrics}
    assert by_name["cvr"].delta_ratio == pytest.approx(0.5)  # (0.3-0.2)/0.2
    assert by_name["aov"].delta_ratio == pytest.approx(-0.5)  # (100-200)/200


def _ev(eid, visitor, etype, amount=None, occurred_at=None):
    e = {"event_id": eid, "visitor_id": visitor, "type": etype}
    if amount is not None:
        e["amount"] = amount
    if occurred_at is not None:
        e["occurred_at"] = occurred_at
    return e


async def test_cohort_api(client: AsyncClient):
    # 2026-01 과 2026-02 에 걸친 구매(기본 윈도우 밖이므로 명시 범위로 조회).
    events = [
        _ev("e1", "v1", "purchase", 100, "2026-01-10T00:00:00Z"),
        _ev("e2", "v1", "purchase", 100, "2026-02-10T00:00:00Z"),
        _ev("e3", "v2", "purchase", 100, "2026-01-15T00:00:00Z"),
    ]
    await client.post("/v1/collect", json={"events": events})
    r = await client.get(
        "/v1/analytics/cohort",
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"},
    )
    assert r.status_code == 200
    rows = {row["cohort"]: row for row in r.json()["rows"]}
    assert rows["2026-01"]["size"] == 2
    assert rows["2026-01"]["cells"][1]["active"] == 1  # v1 재구매


async def test_benchmark_api_isolation(client: AsyncClient):
    # tenant-a: cvr 높음 / tenant-b: cvr 낮음. 벤치마크(플랫폼 평균) 대비 비교.
    await client.post("/v1/collect", json={"events": [
        _ev("a1", "av1", "view"),
        _ev("a2", "av1", "purchase", 100),
    ]})  # tenant-a: cvr 1.0
    await client.post(
        "/v1/collect",
        headers={"X-Sunrise-Key": "other-key"},
        json={"events": [
            _ev("b1", "bv1", "view"),
            _ev("b2", "bv2", "view"),
        ]},
    )  # tenant-b: cvr 0.0
    r = await client.get("/v1/analytics/benchmark")
    assert r.status_code == 200
    by_name = {m["name"]: m for m in r.json()["metrics"]}
    # tenant-a cvr(1.0) 는 플랫폼 평균보다 높아야 한다.
    assert by_name["cvr"]["tenant"] == pytest.approx(1.0)
    assert by_name["cvr"]["delta_ratio"] > 0


async def test_inflow_revenue_segments_and_datatalk_api(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                _ev("inf-1", "v1", "view", occurred_at="2026-06-01T00:00:00Z"),
                {
                    **_ev("inf-2", "v1", "campaign_impression", occurred_at="2026-06-01T00:01:00Z"),
                    "session_id": "s1",
                    "utm_medium": "kakao",
                },
                {
                    **_ev("inf-3", "v1", "campaign_click", occurred_at="2026-06-01T00:02:00Z"),
                    "session_id": "s1",
                    "utm_medium": "kakao",
                },
                {
                    **_ev("inf-4", "v1", "purchase", 100, "2026-06-01T00:03:00Z"),
                    "session_id": "s1",
                    "order_id": "o1",
                    "utm_medium": "kakao",
                },
                {
                    **_ev("inf-4-dup-order", "v1", "purchase", 100, "2026-06-01T00:03:30Z"),
                    "session_id": "s1",
                    "order_id": "o1",
                    "utm_medium": "kakao",
                },
                {
                    **_ev("inf-5", "v2", "view", occurred_at="2026-06-01T00:00:00Z"),
                    "session_id": "s2",
                    "utm_medium": "organic",
                },
                {
                    **_ev("inf-6", "v2", "purchase", 50, "2026-06-01T00:04:00Z"),
                    "session_id": "s2",
                    "order_id": "o2",
                    "utm_medium": "organic",
                },
            ]
        },
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-02T00:00:00Z"}

    inflow = await client.get("/v1/analytics/inflow", params=params)
    revenue = await client.get("/v1/analytics/revenue-breakdown", params=params)
    segments = await client.get("/v1/analytics/segments", params=params)
    datatalk = await client.get("/v1/analytics/datatalk", params=params)

    assert inflow.status_code == 200
    by_channel = {row["channel"]: row for row in inflow.json()["channels"]}
    assert by_channel["kakao"]["revenue"] == 100.0
    assert by_channel["kakao"]["session_count"] == 1
    assert by_channel["kakao"]["purchaser_count"] == 1
    assert by_channel["organic"]["purchase_count"] == 1
    assert revenue.status_code == 200
    assert revenue.json()["total_revenue"] == 150.0
    assert revenue.json()["onsite_revenue"] == 100.0
    assert revenue.json()["attributed_revenue"] == 100.0
    assert segments.status_code == 200
    by_visitor = {row["visitor_id"]: row for row in segments.json()["segments"]}
    assert by_visitor["v1"]["visit_segment"] == "visit_active"
    assert by_visitor["v1"]["purchase_segment"] == "purchase_active"
    assert datatalk.status_code == 200
    assert datatalk.json()["metrics"]["revenue"] == 150.0
    assert datatalk.json()["metrics"]["session_count"] == 2


# ---- 캐시 동작 (FakeCache 주입) ----
async def test_cache_is_used():
    from app.core.cache import Cache
    from app.core.database import init_models
    from app.main import create_app

    class FakeCache(Cache):
        def __init__(self):
            self.store: dict[str, str] = {}
            self.sets = 0
            self.keys: list[str] = []

        async def get(self, key):
            self.keys.append(key)
            return self.store.get(key)

        async def set(self, key, value, ttl_seconds):
            self.sets += 1
            self.store[key] = value

    fake = FakeCache()
    await init_models()
    app = create_app()

    from app.core.cache import get_cache
    app.dependency_overrides[get_cache] = lambda: fake

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as c:
        await c.post("/v1/collect", json={"events": [_ev("e1", "v1", "view")]})
        r1 = await c.get("/v1/analytics/metrics")
        r2 = await c.get("/v1/analytics/metrics")  # 두 번째는 캐시 hit

    assert r1.json() == r2.json()
    assert fake.sets == 1  # 최초 miss 때 1회만 저장, 두 번째는 hit
    assert fake.keys[0].startswith("analytics-response.v1:tenant-a:metrics:")
