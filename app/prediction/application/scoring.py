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


def _is_cold_start(features: VisitorFeatures) -> bool:
    return (
        features.view_count == 0
        and features.cart_add_count == 0
        and features.purchase_count == 0
        and features.revenue == 0.0
        and features.last_seen_at is None
        and features.last_purchase_at is None
    )
