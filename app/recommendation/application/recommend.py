"""Recommendation use cases backed by an ML-style ranking model.

The implementation keeps candidate generation separate from ranking. Candidate
generation still reads event-derived product signals, while ranking now builds a
feature vector and scores each candidate with a logistic model.
"""

from __future__ import annotations

from datetime import datetime
from math import exp, log1p

from app.recommendation.domain.model import (
    Candidate,
    ModelScore,
    Placement,
    PlacementPolicy,
    Recommendation,
    RecommendationFeatures,
    RecommendationMetadata,
    RecommendationModelArtifact,
    VisitorContext,
    clamp01,
)
from app.recommendation.domain.repository import RecommendationRepository


def candidate_pool_size(limit: int) -> int:
    """제외 후에도 limit 를 채울 수 있도록 후보 풀을 넉넉히 잡는다."""
    return max(limit * 10, 100)


def metadata(
    generated_at: datetime, model: RecommendationModelArtifact
) -> RecommendationMetadata:
    return RecommendationMetadata(
        model_version=model.model_version,
        feature_version=model.feature_version,
        generated_at=generated_at,
    )


def _cap_log(value: int, cap: int) -> float:
    """Count feature를 0..1 로그 스케일로 압축한다."""
    return clamp01(log1p(max(value, 0)) / log1p(cap))


def _ratio_signal(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or denominator <= 0:
        return 0.0
    return clamp01((numerator / denominator) / 2.0)


def _discount_signal(price: float | None, original_price: float | None) -> float:
    if price is None or original_price is None or original_price <= 0:
        return 0.0
    return clamp01((original_price - price) / original_price)


def _margin_signal(gross_margin: float | None, price: float | None) -> float:
    if gross_margin is None:
        return 0.0
    if 0 <= gross_margin <= 1:
        return clamp01(gross_margin)
    if price is None or price <= 0:
        return 0.0
    return clamp01(gross_margin / price)


class GenerateCandidates:
    """후보 생성: tenant event 신호에서 상품 후보를 만든다."""

    def __init__(self, repository: RecommendationRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        pool_size: int,
    ) -> list[Candidate]:
        stats = await self._repository.popular_products(tenant_id, start, end, pool_size)
        return [
            Candidate(
                product_id=stat.product_id,
                category=stat.category,
                stat=stat,
            )
            for stat in stats
        ]


class BuildRecommendationFeatures:
    """후보 상품과 visitor context를 ML feature vector로 변환한다."""

    def execute(
        self,
        candidate: Candidate,
        context: VisitorContext,
        placement: Placement,
    ) -> RecommendationFeatures:
        stat = candidate.stat
        return RecommendationFeatures(
            product_id=candidate.product_id,
            category=candidate.category,
            view_signal=_cap_log(stat.view_count, 50),
            cart_signal=_cap_log(stat.cart_add_count, 20),
            purchase_signal=_cap_log(stat.purchase_count, 20),
            buyer_signal=_cap_log(stat.buyer_count, 20),
            category_affinity=(
                1.0
                if candidate.category is not None
                and candidate.category in context.engaged_categories
                else 0.0
            ),
            previously_viewed=(
                1.0 if candidate.product_id in context.viewed_product_ids else 0.0
            ),
            placement_message=1.0 if placement == Placement.MESSAGE else 0.0,
            placement_onsite=1.0 if placement == Placement.ONSITE else 0.0,
            relative_value_signal=_ratio_signal(stat.category_avg_price, stat.price),
            discount_signal=_discount_signal(stat.price, stat.original_price),
            rating_signal=clamp01((stat.rating or 0.0) / 5.0),
            review_confidence=_cap_log(stat.review_count or 0, 500),
            return_quality_signal=(
                clamp01(1.0 - stat.return_rate)
                if stat.return_rate is not None
                else 0.0
            ),
            margin_signal=_margin_signal(stat.gross_margin, stat.price),
        )


class LogisticRecommendationModel:
    """Logistic regression scorer backed by a loaded model artifact."""

    def __init__(self, artifact: RecommendationModelArtifact) -> None:
        self._artifact = artifact

    def predict(self, features: RecommendationFeatures) -> ModelScore:
        contributions = {
            name: getattr(features, name) * weight
            for name, weight in self._artifact.weights.items()
        }
        logit = self._artifact.bias + sum(contributions.values())
        probability = clamp01(1.0 / (1.0 + exp(-logit)))
        reason = self._reason(contributions)
        return ModelScore(
            product_id=features.product_id,
            category=features.category,
            probability=probability,
            reason=reason,
        )

    @staticmethod
    def _reason(contributions: dict[str, float]) -> str:
        drivers = [
            name
            for name, value in sorted(
                contributions.items(), key=lambda item: item[1], reverse=True
            )
            if value > 0
        ][:3]
        return "ml:" + ",".join(drivers or ["bias"])


class RankRecommendations:
    """랭킹: business filter → ML feature vector → logistic model score."""

    def __init__(
        self,
        model_artifact: RecommendationModelArtifact,
        feature_builder: BuildRecommendationFeatures | None = None,
        model: LogisticRecommendationModel | None = None,
    ) -> None:
        self._feature_builder = feature_builder or BuildRecommendationFeatures()
        self._model = model or LogisticRecommendationModel(model_artifact)

    def execute(
        self,
        candidates: list[Candidate],
        context: VisitorContext,
        policy: PlacementPolicy,
        out_of_stock: frozenset[str],
    ) -> list[Recommendation]:
        kept = [c for c in candidates if self._keep(c, context, policy, out_of_stock)]
        scored = [
            self._model.predict(
                self._feature_builder.execute(c, context, policy.placement)
            )
            for c in kept
        ]
        ranked = [
            Recommendation(
                product_id=s.product_id,
                category=s.category,
                score=s.probability,
                reason=s.reason,
            )
            for s in scored
        ]
        ranked.sort(key=lambda r: (-r.score, r.product_id))
        return ranked[: policy.limit]

    @staticmethod
    def _keep(
        candidate: Candidate,
        context: VisitorContext,
        policy: PlacementPolicy,
        out_of_stock: frozenset[str],
    ) -> bool:
        pid = candidate.product_id
        if policy.exclude_purchased and pid in context.purchased_product_ids:
            return False
        if policy.exclude_viewed and pid in context.viewed_product_ids:
            return False
        if policy.exclude_out_of_stock and pid in out_of_stock:
            return False
        if policy.exclude_out_of_stock and not candidate.stat.in_stock:
            return False
        return True


class RecommendItems:
    """orchestrator: 컨텍스트 조회 → 후보 생성 → ML 랭킹."""

    def __init__(
        self,
        repository: RecommendationRepository,
        model_artifact: RecommendationModelArtifact,
    ) -> None:
        self._repository = repository
        self._model_artifact = model_artifact

    async def execute(
        self,
        tenant_id: str,
        visitor_id: str,
        policy: PlacementPolicy,
        out_of_stock: frozenset[str],
        start: datetime,
        end: datetime,
    ) -> tuple[RecommendationMetadata, list[Recommendation]]:
        context = await self._repository.visitor_context(
            tenant_id, visitor_id, start, end
        )
        candidates = await GenerateCandidates(self._repository).execute(
            tenant_id, start, end, candidate_pool_size(policy.limit)
        )
        items = RankRecommendations(self._model_artifact).execute(
            candidates, context, policy, out_of_stock
        )
        return metadata(end, self._model_artifact), items
