"""Prediction API 테스트."""

from __future__ import annotations

from httpx import AsyncClient

from app.prediction.adapters.model_registry import load_prediction_model
from app.prediction.application.scoring import GetPurchaseScores
from app.prediction.domain.model import VisitorFeatures


def _event(
    event_id: str,
    visitor_id: str,
    event_type: str,
    *,
    amount: float | None = None,
    product_id: str | None = None,
    category: str | None = None,
    occurred_at: str = "2026-06-01T00:00:00Z",
):
    event = {
        "event_id": event_id,
        "visitor_id": visitor_id,
        "type": event_type,
        "occurred_at": occurred_at,
    }
    if amount is not None:
        event["amount"] = amount
    if product_id is not None:
        event["product_id"] = product_id
    if category is not None:
        event["category"] = category
    return event


async def _seed_prediction_events(client: AsyncClient):
    return await client.post(
        "/v1/collect",
        json={
            "events": [
                _event("e1", "v1", "view", product_id="p1"),
                _event("e2", "v1", "view", product_id="p1"),
                _event("e3", "v1", "cart_add", product_id="p1"),
                _event("e4", "v1", "purchase", amount=100, product_id="p1"),
                _event("e5", "v1", "view", product_id="p2"),
                _event("e6", "v2", "view", category="shoes"),
                _event("e7", "v2", "cart_add", category="shoes"),
                _event(
                    "e8",
                    "stale",
                    "purchase",
                    amount=50,
                    product_id="old",
                    occurred_at="2026-01-01T00:00:00Z",
                ),
            ]
        },
    )


async def test_purchase_score_rule_ranks_behavior():
    class Repo:
        async def visitor_features(self, tenant_id, visitor_ids, start, end):
            return [
                VisitorFeatures("cart", 2, 1, 0, 0.0, None, None),
                VisitorFeatures("unknown", 0, 0, 0, 0.0, None, None),
            ]

    use_case = GetPurchaseScores(Repo(), load_prediction_model())
    _, scores = await use_case.execute("t1", ["cart", "unknown"], None, None)

    assert scores[0].score > scores[1].score
    assert scores[0].band in {"low", "medium"}
    assert scores[1].band == "low"


async def test_purchase_score_api(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/purchase-score",
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
        },
        json={"visitor_ids": ["v1", "v2", "unknown"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "prediction-response.v1"
    assert body["metadata"]["model_version"] == "ml.logistic-prediction.v3"
    assert body["metadata"]["feature_version"] == "events-ml-features.v1"
    by_visitor = {score["visitor_id"]: score for score in body["scores"]}
    assert by_visitor["v1"]["score"] > by_visitor["v2"]["score"]
    assert by_visitor["unknown"]["band"] == "low"


async def test_prediction_model_status_api(client: AsyncClient):
    response = await client.get("/v1/predictions/model-status")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "prediction-response.v1"
    assert body["model_version"] == "ml.logistic-prediction.v3"
    assert body["readiness"] == "ready"
    assert body["drift_status"] == "baseline_configured"
    assert body["trained_at"] is not None  # 실제 학습 시각(고정값 아님)
    assert body["model_age_days"] >= 0
    assert body["drift_baseline"]["view_signal"] > 0
    assert {"purchase_score", "churn_risk", "product_affinity"} <= set(body["heads"])
    assert body["metrics"]["purchase_auc"] >= 0.5
    # 실제 holdout backtest 지표가 노출된다(in-sample 아님).
    assert body["backtest"]["holdout_size"] > 0
    assert body["backtest"]["purchase_auc"] >= 0.5


async def test_prediction_explain_api_returns_feature_contributions(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/explain",
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
        },
        json={"visitor_id": "v1", "target": "purchase_score"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "prediction-response.v1"
    assert body["visitor_id"] == "v1"
    assert body["target"] == "purchase_score"
    assert body["score"] > 0
    assert body["band"] in {"low", "medium", "high"}
    assert body["contributions"]
    assert body["contributions"][0]["feature"]
    assert body["top_reasons"]


async def test_churn_risk_api(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/churn-risk",
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
        },
        json={"visitor_ids": ["v1", "stale", "unknown"]},
    )

    assert response.status_code == 200
    by_visitor = {risk["visitor_id"]: risk for risk in response.json()["risks"]}
    # 휴면(stale) 방문자는 활성(v1)보다 이탈 위험이 높아야 한다.
    assert by_visitor["stale"]["risk"] > by_visitor["v1"]["risk"]
    # 위험이 높을수록 재타게팅 시점이 빨라야 한다(≤3일).
    assert by_visitor["stale"]["recommended_retargeting_days"] <= 3
    # cold-start(unknown)는 위험 0, 기본 재타게팅 7일.
    assert by_visitor["unknown"]["risk"] == 0.0
    assert by_visitor["unknown"]["recommended_retargeting_days"] == 7


async def test_clv_api_returns_survival_and_predicted_value(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/clv",
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
            "horizon_days": 180,
        },
        json={"visitor_ids": ["v1", "v2", "unknown"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "prediction-response.v1"
    assert body["horizon_days"] == 180
    by_visitor = {value["visitor_id"]: value for value in body["values"]}
    assert by_visitor["v1"]["survival_probability"] > by_visitor["unknown"]["survival_probability"]
    assert by_visitor["v1"]["predicted_clv"] > by_visitor["v2"]["predicted_clv"]
    assert "recent_purchase" in by_visitor["v1"]["reasons"]


async def test_product_affinity_api(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/product-affinity",
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
        },
        json={"visitor_id": "v1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "prediction-response.v1"
    affinities = body["affinities"]
    assert affinities[0]["key"] == "p1"
    assert affinities[0]["score"] > affinities[1]["score"]


async def test_product_affinity_filters_keys(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/product-affinity",
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
        },
        json={"visitor_id": "v1", "keys": ["p2"]},
    )

    assert response.status_code == 200
    assert [item["key"] for item in response.json()["affinities"]] == ["p2"]


async def test_prediction_tenant_isolation(client: AsyncClient):
    await _seed_prediction_events(client)

    response = await client.post(
        "/v1/predictions/purchase-score",
        headers={"X-Sunrise-Key": "other-key"},
        params={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-06-03T00:00:00Z",
        },
        json={"visitor_ids": ["v1"]},
    )

    assert response.status_code == 200
    score = response.json()["scores"][0]
    assert score["visitor_id"] == "v1"
    assert score["score"] < 0.2
    assert score["band"] == "low"
