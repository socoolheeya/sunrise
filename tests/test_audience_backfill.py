"""Audience profile feature 백필(P1-3) 검증.

이벤트 도출 필드 + 상품 feature join 필드 + 외부 필드 미지원 표면화 + 카탈로그 메타.
"""

from __future__ import annotations

from httpx import AsyncClient

WIDE = {"start": "2026-01-01T00:00:00Z", "end": "2026-07-01T00:00:00Z"}


async def _preview(client: AsyncClient, rule: dict):
    return await client.post("/v1/audiences/preview", params=WIDE, json={"rule": rule})


# ---- 이벤트 도출 필드 ----
async def test_event_derived_profile_fields(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            # v-multi: 2개 카테고리, 2회 구매(10일 간격)
            {"event_id": "m1", "visitor_id": "v-multi", "type": "purchase",
             "order_id": "o1", "amount": 100, "product_id": "p1", "category": "cat-a",
             "occurred_at": "2026-06-01T00:00:00Z"},
            {"event_id": "m2", "visitor_id": "v-multi", "type": "purchase",
             "order_id": "o2", "amount": 100, "product_id": "p2", "category": "cat-b",
             "occurred_at": "2026-06-11T00:00:00Z"},
            # v-single: 1개 카테고리 1회 구매
            {"event_id": "s1", "visitor_id": "v-single", "type": "purchase",
             "order_id": "o3", "amount": 50, "product_id": "p1", "category": "cat-a",
             "occurred_at": "2026-06-01T00:00:00Z"},
            # v-browser: 조회만(저관여), 캠페인 노출 없음
            {"event_id": "b1", "visitor_id": "v-browser", "type": "view",
             "product_id": "p1", "occurred_at": "2026-06-01T00:00:00Z"},
        ]},
    )

    # category_purchase_count >= 2 → v-multi 만
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "category_purchase_count", "op": "gte", "value": 2}]})
    assert r.json()["sample_visitor_ids"] == ["v-multi"]

    # cross_sell_score >= 0.5 → v-multi (2개 카테고리)
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "cross_sell_score", "op": "gte", "value": 0.5}]})
    assert r.json()["sample_visitor_ids"] == ["v-multi"]

    # expected_repurchase_days <= 30 → v-multi(10일) 만 (구매 1회는 미정의)
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "expected_repurchase_days", "op": "lte", "value": 30}]})
    assert r.json()["sample_visitor_ids"] == ["v-multi"]

    # lifetime_value >= 200 → v-multi(200) 만
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "lifetime_value", "op": "gte", "value": 200}]})
    assert r.json()["sample_visitor_ids"] == ["v-multi"]

    # engagement_score < 0.3 → v-browser(조회 1회) 포함
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "engagement_score", "op": "lt", "value": 0.3}]})
    assert "v-browser" in r.json()["sample_visitor_ids"]


async def test_recent_campaign_exposure_days(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "ce-v", "visitor_id": "v-exposed", "type": "campaign_impression",
             "category": "camp-1", "occurred_at": "2026-06-28T00:00:00Z"},
            {"event_id": "ce-p", "visitor_id": "v-exposed", "type": "view",
             "occurred_at": "2026-06-28T00:00:00Z"},
            {"event_id": "ne-p", "visitor_id": "v-clean", "type": "view",
             "occurred_at": "2026-06-28T00:00:00Z"},
        ]},
    )
    # end(2026-07-01) 기준 최근 노출 없는 고객만 holdout 가능(>=14일)
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "recent_campaign_exposure_days", "op": "gte", "value": 14}]})
    ids = r.json()["sample_visitor_ids"]
    assert "v-clean" in ids and "v-exposed" not in ids


# ---- 상품 feature join 필드 ----
async def test_product_join_profile_fields(client: AsyncClient):
    await client.post(
        "/v1/recommendations/products",
        json={"products": [
            {"product_id": "p-disc", "category": "cat", "price": 70,
             "original_price": 100, "gross_margin": 0.5, "return_rate": 0.02},
            {"product_id": "p-full", "category": "cat", "price": 100,
             "original_price": 100, "gross_margin": 0.1, "return_rate": 0.2},
        ]},
    )
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "ps-1", "visitor_id": "v-shopper", "type": "purchase",
             "order_id": "o1", "amount": 70, "product_id": "p-disc", "category": "cat",
             "occurred_at": "2026-06-01T00:00:00Z"},
            {"event_id": "ps-2", "visitor_id": "v-shopper", "type": "purchase",
             "order_id": "o2", "amount": 100, "product_id": "p-full", "category": "cat",
             "occurred_at": "2026-06-02T00:00:00Z"},
        ]},
    )

    # discount_affinity = 1할인/2 = 0.5
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "discount_affinity", "op": "gte", "value": 0.5}]})
    assert r.json()["sample_visitor_ids"] == ["v-shopper"]

    # full_price_purchase_count = 1 (p-full)
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "full_price_purchase_count", "op": "gte", "value": 1}]})
    assert r.json()["sample_visitor_ids"] == ["v-shopper"]

    # high_margin_purchase_count = 1 (p-disc, margin 0.5>=0.4)
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "high_margin_purchase_count", "op": "eq", "value": 1}]})
    assert r.json()["sample_visitor_ids"] == ["v-shopper"]

    # return_rate 평균 = (0.02+0.2)/2 = 0.11 → lte 0.05 미충족
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "return_rate", "op": "lte", "value": 0.05}]})
    assert r.json()["sample_visitor_ids"] == []
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "return_rate", "op": "lte", "value": 0.2}]})
    assert r.json()["sample_visitor_ids"] == ["v-shopper"]

    # premium_affinity: 카테고리 평균가 85, p-full(100)>85 → 1/2 = 0.5
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "premium_affinity", "op": "gte", "value": 0.5}]})
    assert r.json()["sample_visitor_ids"] == ["v-shopper"]


async def test_product_join_unsupported_without_product_features(client: AsyncClient):
    """상품 feature 미적재 시 product-join 필드는 unsupported 로 표면화(빈 모수 silent 금지)."""
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "np-1", "visitor_id": "v1", "type": "purchase",
             "order_id": "o1", "amount": 70, "product_id": "p-x", "category": "cat",
             "occurred_at": "2026-06-01T00:00:00Z"},
        ]},
    )
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "discount_affinity", "op": "gte", "value": 0.5}]})
    body = r.json()
    assert body["sample_visitor_ids"] == []
    assert "profile:discount_affinity" in body["unsupported_conditions"]


# ---- 외부 필드 미지원 표면화 ----
async def test_external_profile_field_is_unsupported(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "ext-1", "visitor_id": "v1", "type": "view",
             "occurred_at": "2026-06-01T00:00:00Z"},
        ]},
    )
    r = await _preview(client, {"all": [
        {"type": "profile", "field": "kakao_opt_in", "op": "eq", "value": True}]})
    body = r.json()
    assert body["sample_visitor_ids"] == []
    assert "profile:kakao_opt_in" in body["unsupported_conditions"]


# ---- 카탈로그 메타 (evaluable / external_fields) ----
async def test_catalog_exposes_evaluable_and_external_fields(client: AsyncClient):
    response = await client.get("/v1/audiences/templates")
    by_id = {t["template_id"]: t for t in response.json()["templates"]}

    # 채널 템플릿: 동의/검증 외부 필드 의존 → 평가 불가
    kakao = by_id["kakao_message_candidate"]
    assert kakao["evaluable"] is False
    assert "kakao_opt_in" in kakao["external_fields"]
    assert "phone_verified" in kakao["external_fields"]

    # 장바구니 이탈 템플릿: 이벤트 기반 → 평가 가능
    cart = by_id["cart_added_no_purchase_24h"]
    assert cart["evaluable"] is True
    assert cart["external_fields"] == []
