"""Prediction use cases backed by a promoted ML artifact.

Feature extraction still uses the repository port, so production can replace
SQL-derived features with a feature store adapter without changing HTTP
contracts or scoring orchestration.
"""

from __future__ import annotations

from datetime import datetime
from math import exp, log1p

from app.prediction.domain.model import (
    ChurnRisk,
    CustomerLifetimeValue,
    ProductAffinity,
    ProductAffinityFeatures,
    PredictionModelArtifact,
    PurchaseScore,
    ScoreMetadata,
    VisitorFeatures,
    VisitorPredictionFeatures,
    clamp_score,
    score_band,
)
from app.prediction.domain.repository import PredictionRepository


def metadata(generated_at: datetime, model: PredictionModelArtifact) -> ScoreMetadata:
    return ScoreMetadata(
        model_version=model.model_version,
        feature_version=model.feature_version,
        generated_at=generated_at,
    )


def _cap_log(value: float, cap: int) -> float:
    return clamp_score(log1p(max(value, 0.0)) / log1p(cap))


def _days_between(generated_at: datetime, seen_at: datetime | None) -> int:
    if seen_at is None:
        return 365
    if generated_at.tzinfo is not None and seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=generated_at.tzinfo)
    if generated_at.tzinfo is None and seen_at.tzinfo is not None:
        generated_at = generated_at.replace(tzinfo=seen_at.tzinfo)
    return max(0, (generated_at - seen_at).days)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


class BuildVisitorPredictionFeatures:
    """Convert event-derived visitor features into the ML feature contract."""

    def execute(
        self, features: VisitorFeatures, generated_at: datetime
    ) -> VisitorPredictionFeatures:
        days_since_seen = _days_between(generated_at, features.last_seen_at)
        days_since_purchase = _days_between(generated_at, features.last_purchase_at)
        recency_days = min(days_since_seen, days_since_purchase)
        return VisitorPredictionFeatures(
            visitor_id=features.visitor_id,
            view_signal=_cap_log(features.view_count, 50),
            cart_signal=_cap_log(features.cart_add_count, 20),
            purchase_signal=_cap_log(features.purchase_count, 20),
            revenue_signal=_cap_log(features.revenue, 1000),
            recency_signal=clamp_score(1.0 - min(recency_days, 90) / 90),
            inactivity_signal=clamp_score(min(days_since_seen, 90) / 90),
        )


class BuildProductAffinityFeatures:
    """Convert product/category signals into the ML affinity feature contract."""

    def execute(
        self, visitor_id: str, key: str, *, view_count: int, cart_add_count: int, purchase_count: int
    ) -> ProductAffinityFeatures:
        return ProductAffinityFeatures(
            visitor_id=visitor_id,
            key=key,
            view_signal=_cap_log(view_count, 20),
            cart_signal=_cap_log(cart_add_count, 10),
            purchase_signal=_cap_log(purchase_count, 10),
        )


class MultiHeadLogisticPredictionModel:
    """Logistic scorer for purchase, churn, and product-affinity heads."""

    def __init__(self, artifact: PredictionModelArtifact) -> None:
        self._artifact = artifact

    def predict_visitor(self, head: str, features: VisitorPredictionFeatures) -> float:
        weights = self._artifact.heads[head]
        logit = self._artifact.biases[head] + sum(
            getattr(features, name) * weight for name, weight in weights.items()
        )
        return clamp_score(_sigmoid(logit))

    def predict_affinity(self, features: ProductAffinityFeatures) -> float:
        weights = self._artifact.heads["product_affinity"]
        logit = self._artifact.biases["product_affinity"] + sum(
            getattr(features, name) * weight for name, weight in weights.items()
        )
        return clamp_score(_sigmoid(logit))


class GetPurchaseScores:
    def __init__(
        self,
        repository: PredictionRepository,
        model_artifact: PredictionModelArtifact,
        feature_builder: BuildVisitorPredictionFeatures | None = None,
        model: MultiHeadLogisticPredictionModel | None = None,
    ) -> None:
        self._repository = repository
        self._model_artifact = model_artifact
        self._feature_builder = feature_builder or BuildVisitorPredictionFeatures()
        self._model = model or MultiHeadLogisticPredictionModel(model_artifact)

    async def execute(
        self,
        tenant_id: str,
        visitor_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> tuple[ScoreMetadata, list[PurchaseScore]]:
        features = await self._repository.visitor_features(tenant_id, visitor_ids, start, end)
        scores = [
            PurchaseScore(
                visitor_id=f.visitor_id,
                score=(
                    score := self._model.predict_visitor(
                        "purchase_score",
                        self._feature_builder.execute(f, end),
                    )
                ),
                band=score_band(score),
            )
            for f in features
        ]
        return metadata(end, self._model_artifact), scores


class GetChurnRisks:
    def __init__(
        self,
        repository: PredictionRepository,
        model_artifact: PredictionModelArtifact,
        feature_builder: BuildVisitorPredictionFeatures | None = None,
        model: MultiHeadLogisticPredictionModel | None = None,
    ) -> None:
        self._repository = repository
        self._model_artifact = model_artifact
        self._feature_builder = feature_builder or BuildVisitorPredictionFeatures()
        self._model = model or MultiHeadLogisticPredictionModel(model_artifact)

    async def execute(
        self,
        tenant_id: str,
        visitor_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> tuple[ScoreMetadata, list[ChurnRisk]]:
        features = await self._repository.visitor_features(tenant_id, visitor_ids, start, end)
        risks: list[ChurnRisk] = []
        for f in features:
            if _is_cold_start(f):
                risk = 0.0
            else:
                risk = self._model.predict_visitor(
                    "churn_risk",
                    self._feature_builder.execute(f, end),
                )
            if risk >= 0.7:
                retarget_days = 1
            elif risk >= 0.35:
                retarget_days = 3
            else:
                retarget_days = 7
            risks.append(
                ChurnRisk(
                    visitor_id=f.visitor_id,
                    risk=risk,
                    band=score_band(risk),
                    recommended_retargeting_days=retarget_days,
                )
            )
        return metadata(end, self._model_artifact), risks


class GetProductAffinities:
    def __init__(
        self,
        repository: PredictionRepository,
        model_artifact: PredictionModelArtifact,
        feature_builder: BuildProductAffinityFeatures | None = None,
        model: MultiHeadLogisticPredictionModel | None = None,
    ) -> None:
        self._repository = repository
        self._model_artifact = model_artifact
        self._feature_builder = feature_builder or BuildProductAffinityFeatures()
        self._model = model or MultiHeadLogisticPredictionModel(model_artifact)

    async def execute(
        self,
        tenant_id: str,
        visitor_id: str,
        keys: list[str] | None,
        start: datetime,
        end: datetime,
    ) -> tuple[ScoreMetadata, list[ProductAffinity]]:
        signals = await self._repository.product_signals(
            tenant_id, visitor_id, keys, start, end
        )
        affinities = [
            ProductAffinity(
                visitor_id=visitor_id,
                key=s.key,
                score=self._model.predict_affinity(
                    self._feature_builder.execute(
                        visitor_id,
                        s.key,
                        view_count=s.view_count,
                        cart_add_count=s.cart_add_count,
                        purchase_count=s.purchase_count,
                    )
                ),
            )
            for s in signals
        ]
        affinities.sort(key=lambda item: item.score, reverse=True)
        return metadata(end, self._model_artifact), affinities


class GetCustomerLifetimeValues:
    """Probabilistic CLV baseline from event-derived purchase history.

    This is a production API contract with a conservative baseline scorer. A
    later model registry artifact can replace the survival/monetary equations
    with BG/NBD, Pareto/NBD, and Gamma-Gamma outputs without changing callers.
    """

    def __init__(
        self,
        repository: PredictionRepository,
        model_artifact: PredictionModelArtifact,
    ) -> None:
        self._repository = repository
        self._model_artifact = model_artifact

    async def execute(
        self,
        tenant_id: str,
        visitor_ids: list[str],
        start: datetime,
        end: datetime,
        horizon_days: int,
    ) -> tuple[ScoreMetadata, list[CustomerLifetimeValue]]:
        features = await self._repository.visitor_features(tenant_id, visitor_ids, start, end)
        values = [
            _clv_from_features(feature, end=end, horizon_days=horizon_days)
            for feature in features
        ]
        values.sort(key=lambda item: item.predicted_clv, reverse=True)
        return metadata(end, self._model_artifact), values


def _is_cold_start(features: VisitorFeatures) -> bool:
    return (
        features.view_count == 0
        and features.cart_add_count == 0
        and features.purchase_count == 0
        and features.revenue == 0.0
        and features.last_seen_at is None
        and features.last_purchase_at is None
    )


def _clv_from_features(
    features: VisitorFeatures,
    *,
    end: datetime,
    horizon_days: int,
) -> CustomerLifetimeValue:
    days_since_purchase = _days_between(end, features.last_purchase_at)
    purchase_count = max(features.purchase_count, 0)
    avg_order_value = features.revenue / purchase_count if purchase_count else 0.0
    frequency_factor = log1p(purchase_count)
    recency_decay = exp(-min(days_since_purchase, 365) / 120)
    survival_probability = clamp_score(
        (0.15 + 0.35 * frequency_factor + 0.50 * recency_decay) / 1.55
    )
    annualized_frequency = frequency_factor * (horizon_days / 365)
    expected_purchases = round(survival_probability * annualized_frequency, 4)
    expected_order_value = round(avg_order_value, 2)
    predicted_clv = round(expected_purchases * expected_order_value, 2)
    reasons: list[str] = []
    if purchase_count == 0:
        reasons.append("no_purchase_history")
    if days_since_purchase <= 30:
        reasons.append("recent_purchase")
    elif days_since_purchase >= 90:
        reasons.append("stale_purchase")
    if avg_order_value >= 100:
        reasons.append("high_order_value")
    if purchase_count >= 2:
        reasons.append("repeat_purchase")
    return CustomerLifetimeValue(
        visitor_id=features.visitor_id,
        survival_probability=survival_probability,
        expected_purchases=expected_purchases,
        expected_order_value=expected_order_value,
        predicted_clv=predicted_clv,
        band=_clv_band(predicted_clv),
        reasons=tuple(reasons),
    )


def _clv_band(predicted_clv: float) -> str:
    if predicted_clv >= 100:
        return "high"
    if predicted_clv >= 25:
        return "medium"
    return "low"
