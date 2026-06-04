"""Recommendation API + domain 테스트."""

from __future__ import annotations

from httpx import AsyncClient

from app.recommendation.adapters.model_registry import load_recommendation_model
from app.recommendation.application.recommend import (
    BuildRecommendationFeatures,
    LogisticRecommendationModel,
    RankRecommendations,
)
from app.recommendation.domain.model import (
    Candidate,
    Placement,
    ProductStat,
    VisitorContext,
    resolve_policy,
)


# ----------------------- 도메인/랭킹 단위 테스트 -----------------------
def _stat(pid, category, views=0, carts=0, purchases=0, buyers=0):
    return ProductStat(
        product_id=pid,
        category=category,
        view_count=views,
        cart_add_count=carts,
        purchase_count=purchases,
        buyer_count=buyers,
    )


def _candidate(pid, category, **stat_kwargs):
    return Candidate(
        product_id=pid,
        category=category,
        stat=_stat(pid, category, **stat_kwargs),
    )


def _context(viewed=(), purchased=(), categories=()):
    return VisitorContext(
        visitor_id="v1",
        viewed_product_ids=frozenset(viewed),
        purchased_product_ids=frozenset(purchased),
        engaged_categories=frozenset(categories),
    )


def test_resolve_policy_uses_placement_defaults_and_overrides():
    widget = resolve_policy(Placement.WIDGET)
    assert widget.limit == 6
    assert widget.exclude_purchased is True
    assert widget.exclude_viewed is False

    message = resolve_policy(Placement.MESSAGE)
    assert message.exclude_viewed is True  # 메시지는 이미 본 상품도 제외

    overridden = resolve_policy(Placement.WIDGET, limit=2, exclude_viewed=True)
    assert overridden.limit == 2
    assert overridden.exclude_viewed is True


def test_ranking_excludes_purchased_viewed_and_out_of_stock():
    candidates = [
        _candidate("p1", "shoes", purchases=5),
        _candidate("p2", "shoes", views=3),
        _candidate("p3", "bags", carts=2),
        _candidate("p4", "bags", views=1),
    ]
    context = _context(viewed=["p2"], purchased=["p1"])
    policy = resolve_policy(
        Placement.WIDGET,
        exclude_viewed=True,
        exclude_purchased=True,
        exclude_out_of_stock=True,
    )

    items = RankRecommendations(load_recommendation_model()).execute(
        candidates, context, policy, frozenset({"p4"})
    )
    pids = [i.product_id for i in items]
    assert pids == ["p3"]  # p1=구매, p2=조회, p4=품절 제외 → p3만


def test_ml_model_scores_category_affinity_as_positive_feature():
    candidates = [
        _candidate("p1", "shoes", purchases=1),
        _candidate("p2", "bags", purchases=1),
    ]
    context = _context(categories=["bags"])
    policy = resolve_policy(Placement.ONSITE)

    items = RankRecommendations(load_recommendation_model()).execute(
        candidates, context, policy, frozenset()
    )
    assert [i.product_id for i in items] == ["p2", "p1"]
    assert items[0].reason.startswith("ml:")
    assert "category_affinity" in items[0].reason
    assert 0.0 <= items[0].score <= 1.0


def test_logistic_model_outputs_probability_from_feature_vector():
    features = BuildRecommendationFeatures().execute(
        _candidate("p1", "bags", views=4, carts=2, purchases=1, buyers=1),
        _context(viewed=["p1"], categories=["bags"]),
        Placement.WIDGET,
    )

    score = LogisticRecommendationModel(load_recommendation_model()).predict(features)

    assert score.product_id == "p1"
    assert 0.0 < score.probability < 1.0
    assert score.reason.startswith("ml:")
    assert "category_affinity" in score.reason


def test_feature_builder_includes_product_value_signals():
    features = BuildRecommendationFeatures().execute(
        Candidate(
            product_id="value-pick",
            category="bags",
            stat=ProductStat(
                product_id="value-pick",
                category="bags",
                view_count=1,
                cart_add_count=0,
                purchase_count=0,
                buyer_count=0,
                price=70,
                original_price=100,
                gross_margin=0.35,
                rating=4.5,
                review_count=200,
                return_rate=0.04,
                category_avg_price=95,
                in_stock=True,
            ),
        ),
        _context(categories=["bags"]),
        Placement.WIDGET,
    )

    assert features.relative_value_signal > 0
    assert features.discount_signal == 0.3
    assert features.rating_signal == 0.9
    assert features.review_confidence > 0
    assert features.return_quality_signal == 0.96
    assert features.margin_signal == 0.35


def test_ranking_truncates_to_limit():
    candidates = [
        _candidate(f"p{i}", "shoes", views=i) for i in range(1, 11)
    ]
    policy = resolve_policy(Placement.WIDGET, limit=3)
    items = RankRecommendations(load_recommendation_model()).execute(
        candidates, _context(), policy, frozenset()
    )
    assert len(items) == 3


# ----------------------- HTTP 통합 테스트 -----------------------
async def _seed(client: AsyncClient):
    def ev(eid, vid, etype, pid=None, cat=None, amount=None):
        e = {
            "event_id": eid,
            "visitor_id": vid,
            "type": etype,
            "occurred_at": "2026-06-01T00:00:00Z",
        }
        if pid:
            e["product_id"] = pid
        if cat:
            e["category"] = cat
        if amount is not None:
            e["amount"] = amount
        return e

    return await client.post(
        "/v1/collect",
        json={
            "events": [
                # p-pop: 여러 명이 구매 → 최고 인기
                ev("e1", "u1", "purchase", "p-pop", "shoes", 100),
                ev("e2", "u2", "purchase", "p-pop", "shoes", 100),
                ev("e3", "u3", "view", "p-pop", "shoes"),
                # p-bag: bags 카테고리 인기
                ev("e4", "u2", "cart_add", "p-bag", "bags"),
                ev("e5", "u3", "view", "p-bag", "bags"),
                # p-tops: 낮은 인기
                ev("e6", "u3", "view", "p-tops", "tops"),
                # 대상 visitor v1: p-pop 을 이미 구매, bags 카테고리에 관심
                ev("e7", "v1", "purchase", "p-pop", "shoes", 100),
                ev("e8", "v1", "view", "p-bag", "bags"),
            ]
        },
    )


async def test_recommend_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/v1/recommendations/items",
        json={"visitor_id": "v1"},
        headers={"X-Sunrise-Key": ""},
    )
    assert resp.status_code == 401


async def test_recommend_validation_error(client: AsyncClient):
    resp = await client.post("/v1/recommendations/items", json={"visitor_id": ""})
    assert resp.status_code == 422


async def test_recommend_excludes_purchased_and_returns_metadata(client: AsyncClient):
    await _seed(client)
    resp = await client.post(
        "/v1/recommendations/items",
        json={"visitor_id": "v1", "placement": "widget"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "recommendation-response.v1"
    assert body["placement"] == "widget"
    assert body["metadata"]["model_version"] == "ml.logistic-recommendation.v3"
    assert body["metadata"]["feature_version"] == "events-product-value-features.v1"
    assert body["metadata"]["generated_at"]

    pids = [i["product_id"] for i in body["items"]]
    assert "p-pop" not in pids  # v1 이 이미 구매 → 제외
    assert "p-bag" in pids
    assert all(i["reason"].startswith("ml:") for i in body["items"])


async def test_recommend_out_of_stock_exclusion(client: AsyncClient):
    await _seed(client)
    resp = await client.post(
        "/v1/recommendations/items",
        json={
            "visitor_id": "v1",
            "placement": "onsite",
            "out_of_stock": ["p-bag"],
            "exclude_out_of_stock": True,
        },
    )
    assert resp.status_code == 200
    pids = [i["product_id"] for i in resp.json()["items"]]
    assert "p-bag" not in pids


async def test_product_features_upsert_adds_value_signals_to_recommendation(
    client: AsyncClient,
):
    await _seed(client)
    feature_response = await client.post(
        "/v1/recommendations/products",
        json={
            "products": [
                {
                    "product_id": "p-bag",
                    "category": "bags",
                    "price": 70,
                    "original_price": 100,
                    "gross_margin": 0.35,
                    "rating": 4.6,
                    "review_count": 250,
                    "return_rate": 0.03,
                    "in_stock": True,
                },
                {
                    "product_id": "p-tops",
                    "category": "tops",
                    "price": 120,
                    "original_price": 125,
                    "gross_margin": 0.15,
                    "rating": 3.7,
                    "review_count": 10,
                    "return_rate": 0.22,
                    "in_stock": False,
                },
            ]
        },
    )
    assert feature_response.status_code == 200
    assert feature_response.json()["accepted"] == 2

    resp = await client.post(
        "/v1/recommendations/items",
        json={
            "visitor_id": "v1",
            "placement": "onsite",
            "exclude_out_of_stock": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    pids = [item["product_id"] for item in body["items"]]
    assert "p-tops" not in pids
    bag = next(item for item in body["items"] if item["product_id"] == "p-bag")
    assert "relative_value_signal" in bag["reason"] or "rating_signal" in bag["reason"]


async def test_recommend_tenant_isolation(client: AsyncClient):
    await _seed(client)  # tenant-a 에 적재
    resp = await client.post(
        "/v1/recommendations/items",
        json={"visitor_id": "v1"},
        headers={"X-Sunrise-Key": "other-key"},  # tenant-b
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []  # 다른 테넌트엔 데이터 없음
