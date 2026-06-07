"""Prediction 도메인 모델.

현재는 rule-based scorer 결과를 표현하지만, model_version/feature_version 을 항상
포함해 추후 ML 모델 서빙으로 교체해도 API 계약을 유지한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VisitorFeatures:
    visitor_id: str
    view_count: int
    cart_add_count: int
    purchase_count: int
    revenue: float
    last_seen_at: datetime | None
    last_purchase_at: datetime | None


@dataclass(frozen=True)
class ProductSignal:
    key: str
    view_count: int
    cart_add_count: int
    purchase_count: int


@dataclass(frozen=True)
class VisitorPredictionFeatures:
    """Visitor-level ML feature vector for purchase/churn heads."""

    visitor_id: str
    view_signal: float
    cart_signal: float
    purchase_signal: float
    revenue_signal: float
    recency_signal: float
    inactivity_signal: float


@dataclass(frozen=True)
class ProductAffinityFeatures:
    """Product/category-level ML feature vector for affinity scoring."""

    visitor_id: str
    key: str
    view_signal: float
    cart_signal: float
    purchase_signal: float


@dataclass(frozen=True)
class ScoreMetadata:
    model_version: str
    feature_version: str
    generated_at: datetime


@dataclass(frozen=True)
class PredictionModelArtifact:
    """Versioned promoted prediction model artifact."""

    model_version: str
    feature_version: str
    model_type: str
    trained_at: datetime | None
    visitor_features: tuple[str, ...]
    affinity_features: tuple[str, ...]
    heads: dict[str, dict[str, float]]
    biases: dict[str, float]
    metrics: dict[str, float]
    training_data: dict[str, str | int | float]
    drift_baseline: dict[str, float]


@dataclass(frozen=True)
class PurchaseScore:
    visitor_id: str
    score: float
    band: str


@dataclass(frozen=True)
class ChurnRisk:
    visitor_id: str
    risk: float
    band: str
    recommended_retargeting_days: int


@dataclass(frozen=True)
class CustomerLifetimeValue:
    visitor_id: str
    survival_probability: float
    expected_purchases: float
    expected_order_value: float
    predicted_clv: float
    band: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ProductAffinity:
    visitor_id: str
    key: str
    score: float


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


def score_band(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"
