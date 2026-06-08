"""AI Agent/Copy API tests."""

from __future__ import annotations

from httpx import AsyncClient


def _event(event_id: str, visitor_id: str, event_type: str, amount: float | None = None):
    event = {
        "event_id": event_id,
        "visitor_id": visitor_id,
        "type": event_type,
        "occurred_at": "2026-06-01T00:00:00Z",
    }
    if amount is not None:
        event["amount"] = amount
    return event


async def _seed_conversion_problem(client: AsyncClient):
    return await client.post(
        "/v1/collect",
        json={
            "events": [
                _event("e1", "v1", "view"),
                _event("e2", "v1", "cart_add"),
                _event("e3", "v2", "view"),
                _event("e4", "v2", "cart_add"),
                _event("e5", "v3", "view"),
                _event("e6", "v4", "view"),
                _event("e7", "buyer", "purchase", 100),
            ]
        },
    )


async def test_ai_routes_are_documented_in_openapi(client: AsyncClient):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/ai/diagnoses/site" in paths
    assert "/v1/ai/suggestions/campaigns" in paths
    assert "/v1/ai/copy" in paths


async def test_site_diagnosis_returns_problem_segments(client: AsyncClient):
    await _seed_conversion_problem(client)

    response = await client.post(
        "/v1/ai/diagnoses/site",
        params={
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json={"focus": "conversion"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "ai-response.v1"
    assert body["metadata"]["model_version"] == "rules.ai-agent-copy.v1"
    assert body["health_score"] < 1.0
    codes = {issue["code"] for issue in body["issues"]}
    assert "view_to_cart_dropoff" in codes
    assert "cart_to_purchase_dropoff" in codes


async def test_site_diagnosis_enforces_tenant_isolation(client: AsyncClient):
    await _seed_conversion_problem(client)

    response = await client.post(
        "/v1/ai/diagnoses/site",
        headers={"X-Sunrise-Key": "other-key"},
        params={
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["issues"][0]["code"] == "no_traffic"


async def test_campaign_suggestions_include_audience_channel_and_goal(
    client: AsyncClient,
):
    await _seed_conversion_problem(client)

    response = await client.post(
        "/v1/ai/suggestions/campaigns",
        params={
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-02T00:00:00Z",
        },
        json={"preferred_channels": ["kakao", "onsite"], "max_suggestions": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "ai-response.v1"
    assert len(body["suggestions"]) == 2
    first = body["suggestions"][0]
    assert first["audience"]
    assert first["channel"] in {"kakao", "onsite"}
    assert first["message_goal"]


async def test_copy_generation_returns_guardrail_and_review_flag(client: AsyncClient):
    response = await client.post(
        "/v1/ai/copy",
        json={
            "brand_tone": "friendly",
            "campaign_goal": "recover abandoned carts",
            "product_name": "Linen Shirt",
            "product_text": "Lightweight summer shirt",
            "image_url": "https://example.com/shirt.jpg",
            "count": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "ai-response.v1"
    assert body["guardrail"]["passed"] is True
    assert body["requires_human_review"] is True
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["headline"]


async def test_copy_generation_requires_auth(client: AsyncClient):
    response = await client.post(
        "/v1/ai/copy",
        headers={"X-Sunrise-Key": ""},
        json={
            "brand_tone": "friendly",
            "campaign_goal": "recover abandoned carts",
        },
    )

    assert response.status_code == 401


async def test_copy_generation_flags_sensitive_claims(client: AsyncClient):
    response = await client.post(
        "/v1/ai/copy",
        json={
            "brand_tone": "friendly",
            "campaign_goal": "guaranteed risk-free sale",
            "product_name": "Serum",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["guardrail"]["passed"] is False
    assert body["requires_human_review"] is True
    assert body["guardrail"]["reasons"]
    assert any(r.startswith("prohibited_claims:") for r in body["guardrail"]["reasons"])


async def test_copy_review_workflow_enqueue_and_decide(client: AsyncClient):
    # human review 가 필요한 카피(이미지 동반) → 검토 큐 적재
    copy = await client.post(
        "/v1/ai/copy",
        json={
            "brand_tone": "friendly",
            "campaign_goal": "recover carts",
            "product_name": "Linen Shirt",
            "image_url": "https://example.com/s.jpg",
            "count": 1,
        },
    )
    review_id = copy.json()["review_id"]
    assert review_id is not None

    pending = await client.get("/v1/ai/reviews", params={"status": "pending"})
    ids = [r["id"] for r in pending.json()["reviews"]]
    assert review_id in ids

    # 승인 전이
    decided = await client.post(
        f"/v1/ai/reviews/{review_id}/decision",
        json={"decision": "approved", "reviewer": "ops@shop", "note": "ok"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    assert decided.json()["reviewer"] == "ops@shop"

    # 더 이상 pending 아님
    pending_after = await client.get("/v1/ai/reviews", params={"status": "pending"})
    assert review_id not in [r["id"] for r in pending_after.json()["reviews"]]

    # 이미 결정된 건 재결정 불가(404)
    again = await client.post(
        f"/v1/ai/reviews/{review_id}/decision", json={"decision": "rejected"}
    )
    assert again.status_code == 404


async def test_clean_copy_does_not_enqueue_review(client: AsyncClient):
    response = await client.post(
        "/v1/ai/copy",
        json={"brand_tone": "friendly", "campaign_goal": "welcome new shoppers",
              "product_name": "Mug", "count": 1},
    )
    body = response.json()
    assert body["requires_human_review"] is False
    assert body["review_id"] is None


async def test_review_decision_validation_and_isolation(client: AsyncClient):
    # 존재하지 않는 review → 404
    missing = await client.post(
        "/v1/ai/reviews/999999/decision", json={"decision": "approved"}
    )
    assert missing.status_code == 404
    # 잘못된 decision 값 → 422 (pydantic Literal)
    bad = await client.post(
        "/v1/ai/reviews/1/decision", json={"decision": "maybe"}
    )
    assert bad.status_code == 422
