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


async def test_audience_template_not_found(client: AsyncClient):
    response = await client.get("/v1/audiences/templates/missing")

    assert response.status_code == 404


async def test_audience_templates_require_auth(client: AsyncClient):
    response = await client.get(
        "/v1/audiences/templates",
        headers={"X-Sunrise-Key": ""},
    )

    assert response.status_code == 401
