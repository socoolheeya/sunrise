"""order_fact read model 테스트 (도메인 fold + SQL 머티리얼라이즈/조회 API)."""

from __future__ import annotations

from datetime import datetime, timezone

from httpx import AsyncClient

from app.analytics.domain.order_fact import (
    OrderEvent,
    fold_order_facts,
    revenue_breakdown_from_facts,
)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 1, hour, minute, tzinfo=timezone.utc)


# ---- 도메인 단위 테스트 (외부 의존 없음) ----
def test_fold_dedupes_orders_and_flags_onsite_and_attribution():
    events = [
        # 캠페인 클릭(구매 30분 전) → o2 기여
        OrderEvent("v2", None, "campaign_click", 0.0, _dt(9, 0), utm_medium="kakao"),
        # o1: 같은 주문이 purchase 이벤트 2건 → 1건으로 dedup, 첫 amount(100) 채택
        OrderEvent("v1", "o1", "purchase", 100.0, _dt(10, 0), session_id="s1"),
        OrderEvent("v1", "o1", "purchase", 100.0, _dt(10, 1), session_id="s1"),
        # o2: session 동반(onsite) + 기여(click 30분 전)
        OrderEvent("v2", "o2", "purchase", 200.0, _dt(9, 30), session_id="s2",
                   utm_medium="kakao"),
        # o3: session 없음 → 숨은 매출, 미기여
        OrderEvent("v3", "o3", "purchase", 50.0, _dt(11, 0)),
    ]

    facts = fold_order_facts("tenant-a", events, attribution_window_hours=24)

    by_order = {f.order_id: f for f in facts}
    assert set(by_order) == {"o1", "o2", "o3"}  # 주문 단위 중복제거
    assert by_order["o1"].amount == 100.0  # 다중 이벤트가 200 으로 합산되지 않음
    assert by_order["o1"].onsite_matched is True
    assert by_order["o2"].attributed is True
    assert by_order["o2"].attributed_channel == "kakao"
    assert by_order["o3"].onsite_matched is False
    assert by_order["o3"].attributed is False

    total, onsite, attributed = revenue_breakdown_from_facts(facts)
    assert total == 350.0
    assert onsite == 300.0  # o1 + o2
    assert attributed == 200.0  # o2


def test_fold_attribution_respects_window():
    events = [
        # 클릭이 구매보다 48시간 전 → 24h window 밖
        OrderEvent("v1", None, "campaign_click", 0.0,
                   datetime(2026, 5, 30, 9, tzinfo=timezone.utc), utm_medium="email"),
        OrderEvent("v1", "o1", "purchase", 100.0, _dt(10), session_id="s1"),
    ]

    facts = fold_order_facts("tenant-a", events, attribution_window_hours=24)

    assert facts[0].attributed is False


def test_fold_without_order_id_treats_each_purchase_as_one_order():
    events = [
        OrderEvent("v1", None, "purchase", 100.0, _dt(10), session_id="s1"),
        OrderEvent("v1", None, "purchase", 50.0, _dt(11), session_id="s1"),
    ]

    facts = fold_order_facts("tenant-a", events)

    assert len(facts) == 2
    assert sum(f.amount for f in facts) == 150.0


# ---- SQL 머티리얼라이즈 + 조회 API 통합 테스트 ----
async def test_order_fact_refresh_and_revenue_breakdown(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {"event_id": "of-click", "visitor_id": "v2", "type": "campaign_click",
                 "occurred_at": "2026-06-01T09:00:00Z", "utm_medium": "kakao"},
                # o1: 동일 주문 purchase 이벤트 2건 (event_id 는 서로 다름)
                {"event_id": "of-1a", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o1", "amount": 100, "session_id": "s1",
                 "occurred_at": "2026-06-01T10:00:00Z"},
                {"event_id": "of-1b", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o1", "amount": 100, "session_id": "s1",
                 "occurred_at": "2026-06-01T10:01:00Z"},
                {"event_id": "of-2", "visitor_id": "v2", "type": "purchase",
                 "order_id": "o2", "amount": 200, "session_id": "s2",
                 "utm_medium": "kakao", "occurred_at": "2026-06-01T09:30:00Z"},
                # o3: session 없음 → 숨은 매출
                {"event_id": "of-3", "visitor_id": "v3", "type": "purchase",
                 "order_id": "o3", "amount": 50,
                 "occurred_at": "2026-06-01T11:00:00Z"},
            ]
        },
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-02T00:00:00Z"}

    refresh = await client.post("/v1/analytics/order-fact/refresh", params=params)
    breakdown = await client.get(
        "/v1/analytics/order-fact/revenue-breakdown", params=params
    )

    assert refresh.status_code == 200
    assert refresh.json()["materialized_orders"] == 3  # o1, o2, o3
    assert breakdown.status_code == 200
    body = breakdown.json()
    assert body["total_revenue"] == 350.0  # o1 의 중복 purchase 이벤트가 합산 안 됨
    assert body["onsite_revenue"] == 300.0  # o1 + o2 (session 동반)
    assert body["hidden_revenue"] == 50.0  # o3
    assert body["attributed_revenue"] == 200.0  # o2 (click 30분 전)


async def test_order_fact_refresh_is_idempotent(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {"event_id": "idem-1", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o1", "amount": 100, "session_id": "s1",
                 "occurred_at": "2026-06-01T10:00:00Z"},
            ]
        },
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-02T00:00:00Z"}

    first = await client.post("/v1/analytics/order-fact/refresh", params=params)
    second = await client.post("/v1/analytics/order-fact/refresh", params=params)
    breakdown = await client.get(
        "/v1/analytics/order-fact/revenue-breakdown", params=params
    )

    assert first.json()["materialized_orders"] == 1
    assert second.json()["materialized_orders"] == 1  # upsert, 중복 행 생성 안 함
    assert breakdown.json()["total_revenue"] == 100.0


async def test_order_fact_refresh_invalidates_breakdown_cache():
    """refresh 후 동일 윈도우 breakdown 캐시가 무효화돼 stale 값을 반환하지 않는다."""
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

    fake = FakeCache()
    await init_models()
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: fake

    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-02T00:00:00Z"}
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as c:
        await c.post(
            "/v1/collect",
            json={"events": [
                {"event_id": "ci-1", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o1", "amount": 100, "session_id": "s1",
                 "occurred_at": "2026-06-01T10:00:00Z"},
            ]},
        )
        await c.post("/v1/analytics/order-fact/refresh", params=params)
        first = await c.get("/v1/analytics/order-fact/revenue-breakdown", params=params)

        # 새 주문 추가 후 재머티리얼라이즈 → 캐시 무효화되어야 함
        await c.post(
            "/v1/collect",
            json={"events": [
                {"event_id": "ci-2", "visitor_id": "v2", "type": "purchase",
                 "order_id": "o2", "amount": 200, "session_id": "s2",
                 "occurred_at": "2026-06-01T11:00:00Z"},
            ]},
        )
        await c.post("/v1/analytics/order-fact/refresh", params=params)
        second = await c.get("/v1/analytics/order-fact/revenue-breakdown", params=params)

    assert first.json()["total_revenue"] == 100.0
    assert second.json()["total_revenue"] == 300.0  # stale 100 이 아님


async def test_order_fact_enforces_tenant_isolation(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {"event_id": "iso-1", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o1", "amount": 100, "session_id": "s1",
                 "occurred_at": "2026-06-01T10:00:00Z"},
            ]
        },
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-02T00:00:00Z"}
    await client.post("/v1/analytics/order-fact/refresh", params=params)

    other = await client.get(
        "/v1/analytics/order-fact/revenue-breakdown",
        params=params,
        headers={"X-Sunrise-Key": "other-key"},
    )

    assert other.status_code == 200
    assert other.json()["total_revenue"] == 0.0  # tenant-b 는 주문 없음
