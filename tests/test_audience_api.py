"""Audience template catalog API tests."""

from __future__ import annotations

from httpx import AsyncClient


async def test_audience_template_routes_are_documented_in_openapi(client: AsyncClient):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/audiences/templates" in paths
    assert "/v1/audiences/templates/{template_id}" in paths


async def test_audience_templates_provide_50_plus_catalog(client: AsyncClient):
    response = await client.get("/v1/audiences/templates")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "audience-response.v1"
    assert body["catalog_version"] == "audience-template-catalog.v1"
    assert body["count"] >= 50
    template_ids = [template["template_id"] for template in body["templates"]]
    assert len(template_ids) == len(set(template_ids))
    categories = {template["category"] for template in body["templates"]}
    assert {"cart", "prediction", "retention", "churn", "channel"} <= categories


async def test_audience_templates_filter_by_category_and_query(client: AsyncClient):
    category_response = await client.get("/v1/audiences/templates?category=cart")
    query_response = await client.get("/v1/audiences/templates?query=구매가능성")

    assert category_response.status_code == 200
    assert category_response.json()["count"] >= 5
    assert all(
        template["category"] == "cart"
        for template in category_response.json()["templates"]
    )
    assert query_response.status_code == 200
    assert query_response.json()["count"] >= 2
    assert any(
        template["template_id"] == "purchase_score_high"
        for template in query_response.json()["templates"]
    )


async def test_audience_template_detail_returns_rule_contract(client: AsyncClient):
    response = await client.get("/v1/audiences/templates/cart_added_no_purchase_24h")

    assert response.status_code == 200
    body = response.json()
    assert body["template_id"] == "cart_added_no_purchase_24h"
    assert body["category"] == "cart"
    assert body["recommended_trigger"] == "cart_recovery"
    assert body["rule"]["all"]


async def test_audience_preview_and_materialize_api(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {
                    "event_id": "aud-1",
                    "visitor_id": "v-cart",
                    "type": "cart_add",
                    "product_id": "p1",
                    "occurred_at": "2026-06-01T00:00:00Z",
                },
                {
                    "event_id": "aud-2",
                    "visitor_id": "v-view",
                    "type": "view",
                    "product_id": "p2",
                    "occurred_at": "2026-06-01T00:00:00Z",
                },
            ]
        },
    )
    payload = {
        "rule": {
            "all": [
                {
                    "type": "event_count",
                    "event": "cart_add",
                    "window_days": 7,
                    "op": "gte",
                    "value": 1,
                }
            ]
        },
        "sample_limit": 10,
    }

    preview = await client.post(
        "/v1/audiences/preview",
        params={
            "start": "2026-05-30T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json=payload,
    )
    materialized = await client.post(
        "/v1/audiences/materialize",
        params={
            "start": "2026-05-30T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json={**payload, "audience_id": "cart-audience"},
    )

    assert preview.status_code == 200
    assert preview.json()["schema_version"] == "audience-response.v1"
    assert preview.json()["matched_count"] == 1
    assert preview.json()["sample_visitor_ids"] == ["v-cart"]
    assert preview.json()["unsupported_conditions"] == []
    assert materialized.status_code == 200
    assert materialized.json()["audience_id"] == "cart-audience"
    assert materialized.json()["member_count"] == 1
    assert materialized.json()["status"] == "active"


async def test_audience_preview_uses_prediction_score_conditions(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {
                    "event_id": "aud-score-1",
                    "visitor_id": "v-hot",
                    "type": "view",
                    "product_id": "p1",
                    "occurred_at": "2026-06-01T00:00:00Z",
                },
                {
                    "event_id": "aud-score-2",
                    "visitor_id": "v-hot",
                    "type": "cart_add",
                    "product_id": "p1",
                    "occurred_at": "2026-06-01T00:05:00Z",
                },
            ]
        },
    )

    response = await client.post(
        "/v1/audiences/preview",
        params={
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json={
            "rule": {
                "all": [
                    {
                        "type": "score",
                        "name": "purchase_score",
                        "op": "gte",
                        "value": 0.1,
                    }
                ]
            },
            "sample_limit": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["sample_visitor_ids"] == ["v-hot"]


async def test_audience_preview_respects_event_count_window(client: AsyncClient):
    """event_count 조건의 window_days 가 실제로 하위 윈도우를 좁히는지 검증."""
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {
                    "event_id": "win-old",
                    "visitor_id": "v-old",
                    "type": "cart_add",
                    "product_id": "p1",
                    # end(2026-06-10) 기준 40일 전 → 7일 윈도우 밖, 90일 윈도우 안
                    "occurred_at": "2026-05-01T00:00:00Z",
                },
                {
                    "event_id": "win-new",
                    "visitor_id": "v-new",
                    "type": "cart_add",
                    "product_id": "p1",
                    "occurred_at": "2026-06-09T00:00:00Z",
                },
            ]
        },
    )
    params = {"start": "2026-03-12T00:00:00Z", "end": "2026-06-10T00:00:00Z"}

    short_window = await client.post(
        "/v1/audiences/preview",
        params=params,
        json={
            "rule": {
                "all": [
                    {"type": "event_count", "event": "cart_add", "window_days": 7,
                     "op": "gte", "value": 1}
                ]
            }
        },
    )
    long_window = await client.post(
        "/v1/audiences/preview",
        params=params,
        json={
            "rule": {
                "all": [
                    {"type": "event_count", "event": "cart_add", "window_days": 90,
                     "op": "gte", "value": 1}
                ]
            }
        },
    )

    assert short_window.status_code == 200
    # 7일 윈도우: 최근(v-new)만 매칭, 40일 전 v-old 는 제외
    assert short_window.json()["matched_count"] == 1
    assert short_window.json()["sample_visitor_ids"] == ["v-new"]
    # 90일 윈도우: 둘 다 매칭
    assert long_window.status_code == 200
    assert long_window.json()["matched_count"] == 2


async def test_audience_template_not_found(client: AsyncClient):
    response = await client.get("/v1/audiences/templates/missing")

    assert response.status_code == 404


async def test_audience_templates_require_auth(client: AsyncClient):
    response = await client.get(
        "/v1/audiences/templates",
        headers={"X-Sunrise-Key": ""},
    )

    assert response.status_code == 401
