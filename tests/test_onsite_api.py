"""Onsite campaign decision and tracking API tests."""

from __future__ import annotations

from httpx import AsyncClient


def _event(event_id: str, visitor_id: str, event_type: str, product_id: str):
    return {
        "event_id": event_id,
        "visitor_id": visitor_id,
        "type": event_type,
        "product_id": product_id,
        "category": "tops",
        "occurred_at": "2026-06-01T00:00:00Z",
    }


async def _seed_recommendation_candidates(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                _event("o1", "u1", "view", "p-shirt"),
                _event("o2", "u2", "cart_add", "p-shirt"),
                _event("o3", "u3", "purchase", "p-shirt"),
                _event("o4", "u4", "view", "p-bag"),
                _event("o5", "u5", "cart_add", "p-bag"),
                _event("o6", "u6", "view", "p-shoes"),
            ]
        },
    )


async def test_onsite_routes_are_documented_in_openapi(client: AsyncClient):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/onsite/decide" in paths
    assert "/v1/onsite/impressions" in paths
    assert "/v1/onsite/clicks" in paths
    assert "/v1/onsite/dismissals" in paths


async def test_onsite_decide_returns_cart_recovery_campaign(client: AsyncClient):
    await _seed_recommendation_candidates(client)

    response = await client.post(
        "/v1/onsite/decide",
        params={
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json={
            "visitor_id": "v-cart",
            "current_event": "exit_intent",
            "page_url": "https://shop.example/cart",
            "placement": "popup",
            "recent": {
                "viewed_product_ids": ["p-shirt"],
                "cart_product_ids": ["p-shirt"],
                "purchased_product_ids": [],
            },
            "limit": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "onsite-decision.v1"
    assert body["eligible"] is True
    assert body["trigger"] == "cart_recovery"
    assert body["campaign_id"] == "onsite-cart_recovery-v1"
    assert body["creative"]["headline"]
    assert len(body["items"]) <= 2


async def test_onsite_decide_can_return_no_eligible_campaign(client: AsyncClient):
    response = await client.post(
        "/v1/onsite/decide",
        json={
            "visitor_id": "v1",
            "current_event": "cart_add",
            "placement": "banner",
            "recent": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is False
    assert body["campaign_id"] is None
    assert body["creative"] is None


async def test_onsite_tracking_events_are_collected_idempotently(client: AsyncClient):
    payload = {
        "decision_id": "decision-1",
        "campaign_id": "onsite-cart_recovery-v1",
        "visitor_id": "v1",
        "product_id": "p-shirt",
        "category": "tops",
        "occurred_at": "2026-06-01T00:00:00Z",
    }

    first = await client.post("/v1/onsite/impressions", json=payload)
    duplicate = await client.post("/v1/onsite/impressions", json=payload)
    click = await client.post("/v1/onsite/clicks", json=payload)
    dismissal = await client.post("/v1/onsite/dismissals", json=payload)

    assert first.status_code == 202
    assert first.json()["accepted"] == 1
    assert duplicate.json()["duplicates"] == 1
    assert click.status_code == 202
    assert dismissal.status_code == 202


async def test_onsite_requires_auth(client: AsyncClient):
    response = await client.post(
        "/v1/onsite/decide",
        headers={"X-Sunrise-Key": ""},
        json={"visitor_id": "v1", "current_event": "exit_intent"},
    )

    assert response.status_code == 401
