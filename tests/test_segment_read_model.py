"""lifecycle segment read model + 세그먼트 이동(transition) 테스트."""

from __future__ import annotations

from httpx import AsyncClient

from app.analytics.domain.model import (
    LifecycleSegment,
    compute_segment_transitions,
)


# ---- 도메인 단위 테스트 (외부 의존 없음) ----
def _seg(vid: str, visit: str, purchase: str = "no_purchase") -> LifecycleSegment:
    return LifecycleSegment(
        visitor_id=vid, visit_segment=visit, purchase_segment=purchase, revenue=0.0
    )


def test_compute_segment_transitions_visit():
    previous = [_seg("v1", "visit_active"), _seg("v2", "visit_active")]
    current = [
        _seg("v1", "visit_risk"),  # active -> risk
        _seg("v2", "visit_active"),  # active -> active
        _seg("v3", "visit_active"),  # 신규(이전 없음) -> active
    ]

    report = compute_segment_transitions(previous, current, "visit")
    by_pair = {(t.from_segment, t.to_segment): t for t in report.transitions}

    assert by_pair[("visit_active", "visit_risk")].customer_count == 1
    assert by_pair[("visit_active", "visit_risk")].transition_rate == 0.5  # 1/2
    assert by_pair[("visit_active", "visit_active")].customer_count == 1
    assert by_pair[("absent", "visit_active")].customer_count == 1  # 신규 유입
    assert by_pair[("absent", "visit_active")].transition_rate == 1.0  # 1/1


def test_top_sources():
    previous = [
        _seg("a", "visit_active"),
        _seg("b", "visit_risk"),
        _seg("c", "visit_risk"),
    ]
    current = [
        _seg("a", "visit_inactive"),
        _seg("b", "visit_inactive"),
        _seg("c", "visit_inactive"),
    ]
    report = compute_segment_transitions(previous, current, "visit")

    sources = report.top_sources("visit_inactive", limit=3)
    # visit_risk -> inactive (2명) 이 visit_active -> inactive (1명) 보다 상위
    assert sources[0].from_segment == "visit_risk"
    assert sources[0].customer_count == 2


# ---- 머티리얼라이즈 + 이동 분석 통합 테스트 ----
async def test_segment_refresh_and_transitions(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                # v1: 06-01 마지막 활동 → 시간이 지나며 active -> risk
                {"event_id": "s-v1", "visitor_id": "v1", "type": "view",
                 "occurred_at": "2026-06-01T00:00:00Z"},
                # v2: 06-10, 06-18 활동 → snap1(06-05) 에는 없고 snap2 에 active 유입
                {"event_id": "s-v2a", "visitor_id": "v2", "type": "view",
                 "occurred_at": "2026-06-10T00:00:00Z"},
                {"event_id": "s-v2b", "visitor_id": "v2", "type": "view",
                 "occurred_at": "2026-06-18T00:00:00Z"},
            ]
        },
    )

    # 스냅샷 #1: as_of=06-05 (window 도 06-05 에서 종료해 미래 이벤트 제외)
    snap1 = await client.post(
        "/v1/analytics/segments/refresh",
        params={"start": "2026-05-01T00:00:00Z", "end": "2026-06-05T00:00:00Z",
                "as_of": "2026-06-05T00:00:00Z"},
    )
    # 스냅샷 #2: as_of=06-20
    snap2 = await client.post(
        "/v1/analytics/segments/refresh",
        params={"start": "2026-05-01T00:00:00Z", "end": "2026-06-20T00:00:00Z",
                "as_of": "2026-06-20T00:00:00Z"},
    )

    transitions = await client.get(
        "/v1/analytics/segments/transitions",
        params={"from": "2026-06-05T00:00:00Z", "to": "2026-06-20T00:00:00Z",
                "segment_type": "visit"},
    )

    assert snap1.status_code == 200
    assert snap1.json()["materialized_customers"] == 1  # window 에 v1 만(06-05 이전)
    assert snap2.json()["materialized_customers"] == 2  # v1, v2
    assert transitions.status_code == 200
    by_pair = {
        (t["from_segment"], t["to_segment"]): t
        for t in transitions.json()["transitions"]
    }
    # v1: snap1 에 active 로 존재 → snap2 에서 risk (19일 경과)
    assert by_pair[("visit_active", "visit_risk")]["customer_count"] == 1
    # v2: snap1 window 에 없어 absent → snap2 active
    assert by_pair[("absent", "visit_active")]["customer_count"] == 1


async def test_segment_transitions_rejects_invalid_type(client: AsyncClient):
    response = await client.get(
        "/v1/analytics/segments/transitions",
        params={"from": "2026-06-05T00:00:00Z", "to": "2026-06-20T00:00:00Z",
                "segment_type": "bogus"},
    )
    assert response.status_code == 422


async def test_segment_refresh_enforces_tenant_isolation(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "iso-seg", "visitor_id": "v1", "type": "view",
             "occurred_at": "2026-06-01T00:00:00Z"},
        ]},
    )
    params = {"start": "2026-05-01T00:00:00Z", "end": "2026-06-05T00:00:00Z",
              "as_of": "2026-06-05T00:00:00Z"}
    await client.post("/v1/analytics/segments/refresh", params=params)

    other = await client.post(
        "/v1/analytics/segments/refresh",
        params=params,
        headers={"X-Sunrise-Key": "other-key"},
    )
    assert other.json()["materialized_customers"] == 0  # tenant-b 는 고객 없음


async def test_segments_refresh_invalidates_transitions_cache():
    """refresh 후 동일 from/to transition 캐시가 무효화돼 stale 을 반환하지 않는다(P1-4)."""
    from httpx import ASGITransport

    from app.core.cache import Cache, get_cache
    from app.core.database import init_models
    from app.main import create_app

    class FakeCache(Cache):
        def __init__(self):
            self.store: dict[str, str] = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ttl_seconds):
            self.store[key] = value

        async def delete(self, key):
            self.store.pop(key, None)

        async def delete_prefix(self, prefix):
            for key in [k for k in self.store if k.startswith(prefix)]:
                self.store.pop(key, None)

    fake = FakeCache()
    await init_models()
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: fake

    win = {"start": "2026-05-01T00:00:00Z"}
    tr = {"from": "2026-06-05T00:00:00Z", "to": "2026-06-20T00:00:00Z",
          "segment_type": "visit"}
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as c:
        await c.post(
            "/v1/collect",
            json={"events": [
                {"event_id": "ci-1", "visitor_id": "v1", "type": "view",
                 "occurred_at": "2026-06-01T00:00:00Z"},
            ]},
        )
        await c.post("/v1/analytics/segments/refresh",
                     params={**win, "end": "2026-06-05T00:00:00Z", "as_of": tr["from"]})
        await c.post("/v1/analytics/segments/refresh",
                     params={**win, "end": "2026-06-20T00:00:00Z", "as_of": tr["to"]})
        first = await c.get("/v1/analytics/segments/transitions", params=tr)
        assert any(
            t["from_segment"] == "visit_active" and t["to_segment"] == "visit_risk"
            for t in first.json()["transitions"]
        )
        assert fake.store  # transition 결과가 캐시됨

        # 새 고객으로 재머티리얼라이즈 → 캐시 무효화되어야 함
        await c.post(
            "/v1/collect",
            json={"events": [
                {"event_id": "ci-2", "visitor_id": "v2", "type": "view",
                 "occurred_at": "2026-06-18T00:00:00Z"},
            ]},
        )
        await c.post("/v1/analytics/segments/refresh",
                     params={**win, "end": "2026-06-20T00:00:00Z", "as_of": tr["to"]})
        second = await c.get("/v1/analytics/segments/transitions", params=tr)

    # v2 가 새로 유입(absent→active) 되어 결과가 갱신됨(stale 아님)
    assert any(t["from_segment"] == "absent" for t in second.json()["transitions"])


async def test_legacy_revenue_breakdown_marked_deprecated(client: AsyncClient):
    """레거시 /revenue-breakdown 이 OpenAPI 에서 deprecated 로 표기된다(P1-5)."""
    spec = (await client.get("/openapi.json")).json()
    legacy = spec["paths"]["/v1/analytics/revenue-breakdown"]["get"]
    successor = spec["paths"]["/v1/analytics/order-fact/revenue-breakdown"]["get"]
    assert legacy.get("deprecated") is True
    assert successor.get("deprecated") is not True
