"""수집 + 분석 API 통합 테스트 (SQLite 백엔드)."""

from __future__ import annotations

from httpx import AsyncClient


def _event(eid, visitor, etype, amount=None):
    e = {"event_id": eid, "visitor_id": visitor, "type": etype}
    if amount is not None:
        e["amount"] = amount
    return e


# 모니토닉 퍼널 + 재구매 시나리오.
SEED_EVENTS = [
    _event("e1", "v1", "view"),
    _event("e2", "v1", "cart_add"),
    _event("e3", "v1", "purchase", 100),
    _event("e4", "v2", "view"),
    _event("e5", "v2", "cart_add"),
    _event("e6", "v2", "purchase", 300),
    _event("e7", "v2", "purchase", 200),  # 재구매
    _event("e8", "v3", "view"),
    _event("e9", "v3", "cart_add"),  # 장바구니 이탈
    _event("e10", "v4", "view"),  # 단순 방문
]


async def _seed(client: AsyncClient):
    return await client.post("/v1/collect", json={"events": SEED_EVENTS})


# ---- 인증 ----
async def test_collect_requires_api_key(client: AsyncClient):
    r = await client.post(
        "/v1/collect",
        json={"events": [_event("e1", "v1", "view")]},
        headers={"X-Sunrise-Key": ""},
    )
    assert r.status_code == 401


async def test_collect_rejects_invalid_key(client: AsyncClient):
    r = await client.post(
        "/v1/collect",
        json={"events": [_event("e1", "v1", "view")]},
        headers={"X-Sunrise-Key": "nope"},
    )
    assert r.status_code == 401


# ---- 수집 ----
async def test_collect_accepts_events(client: AsyncClient):
    r = await _seed(client)
    assert r.status_code == 202
    body = r.json()
    assert body["schema_version"] == "tracking-event.v1"
    assert body["accepted"] == 10
    assert body["duplicates"] == 0


async def test_collect_accepts_explicit_schema_version(client: AsyncClient):
    r = await client.post(
        "/v1/collect",
        json={
            "schema_version": "tracking-event.v1",
            "events": [_event("e1", "v1", "view")],
        },
    )
    assert r.status_code == 202
    assert r.json()["accepted"] == 1


async def test_collect_rejects_unsupported_schema_version(client: AsyncClient):
    r = await client.post(
        "/v1/collect",
        json={
            "schema_version": "tracking-event.v0",
            "events": [_event("e1", "v1", "view")],
        },
    )
    assert r.status_code == 422


async def test_collect_is_idempotent(client: AsyncClient):
    await _seed(client)
    r = await _seed(client)  # 동일 event_id 재전송
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 0
    assert body["duplicates"] == 10


async def test_collect_rejects_payload_over_byte_limit(client: AsyncClient):
    from app.core.config import get_settings

    get_settings().max_collect_payload_bytes = 10

    r = await client.post(
        "/v1/collect",
        json={"events": [_event("e1", "v1", "view")]},
    )

    assert r.status_code == 413
    assert r.json()["detail"] == "max collect payload is 10 bytes"


async def test_collect_rate_limits_per_tenant(client: AsyncClient):
    from app.core.config import get_settings

    get_settings().collect_rate_limit_per_minute = 1

    first = await client.post(
        "/v1/collect",
        json={"events": [_event("e1", "v1", "view")]},
    )
    second = await client.post(
        "/v1/collect",
        json={"events": [_event("e2", "v1", "view")]},
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"] == "collect rate limit exceeded"


async def test_purchase_without_amount_is_422(client: AsyncClient):
    r = await client.post(
        "/v1/collect",
        json={"events": [_event("e1", "v1", "purchase")]},
    )
    assert r.status_code == 422


# ---- 지표 ----
async def test_metrics_dashboard(client: AsyncClient):
    await _seed(client)
    r = await client.get("/v1/analytics/metrics")
    assert r.status_code == 200
    m = r.json()
    assert m["schema_version"] == "analytics-response.v1"
    assert m["visitor_count"] == 4
    assert m["purchase_count"] == 3
    assert m["revenue"] == 600.0
    assert m["cvr"] == 0.5  # 구매자 2 / 방문자 4
    assert m["aov"] == 200.0  # 600 / 3
    assert m["repeat_rate"] == 0.5  # 재구매자 1 / 구매자 2


# ---- 퍼널 ----
async def test_funnel(client: AsyncClient):
    await _seed(client)
    r = await client.get("/v1/analytics/funnel")
    assert r.status_code == 200
    f = r.json()
    assert f["schema_version"] == "analytics-response.v1"
    names = {s["name"]: s["visitors"] for s in f["steps"]}
    assert names == {"조회": 4, "장바구니": 3, "구매": 2}
    assert f["overall_conversion"] == 0.5


# ---- 멀티테넌트 격리 ----
async def test_tenant_isolation(client: AsyncClient):
    await _seed(client)  # tenant-a (test-key)
    r = await client.get(
        "/v1/analytics/metrics", headers={"X-Sunrise-Key": "other-key"}
    )
    assert r.status_code == 200
    m = r.json()
    assert m["visitor_count"] == 0
    assert m["revenue"] == 0.0


async def test_openapi_documents_response_schema_versions(client: AsyncClient):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schemas = r.json()["components"]["schemas"]
    assert "schema_version" in schemas["CollectResponse"]["properties"]
    assert "schema_version" in schemas["DashboardResponse"]["properties"]


async def test_ops_metrics_exposes_ingestion_counters(client: AsyncClient):
    await _seed(client)
    await _seed(client)

    r = await client.get("/ops/metrics")

    assert r.status_code == 200
    assert r.json()["ingestion"] == {
        "accepted_events": 10,
        "duplicate_events": 10,
        "publish_failures": 0,
        "dlq_published": 0,
        "dlq_failures": 0,
    }
