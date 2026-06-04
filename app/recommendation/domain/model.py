"""Recommendation domain models for ML ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Placement(str, Enum):
    """추천을 노출하는 caller 위치."""

    WIDGET = "widget"
    MESSAGE = "message"
    ONSITE = "onsite"


@dataclass(frozen=True)
class PlacementPolicy:
    """caller(placement)별 추천 정책."""

    placement: Placement
    limit: int
    exclude_viewed: bool
    exclude_purchased: bool
    exclude_out_of_stock: bool


_PLACEMENT_DEFAULTS: dict[Placement, PlacementPolicy] = {
    Placement.WIDGET: PlacementPolicy(
        Placement.WIDGET,
        limit=6,
        exclude_viewed=False,
        exclude_purchased=True,
        exclude_out_of_stock=True,
    ),
    Placement.MESSAGE: PlacementPolicy(
        Placement.MESSAGE,
        limit=3,
        exclude_viewed=True,
        exclude_purchased=True,
        exclude_out_of_stock=True,
    ),
    Placement.ONSITE: PlacementPolicy(
        Placement.ONSITE,
        limit=10,
        exclude_viewed=False,
        exclude_purchased=True,
        exclude_out_of_stock=True,
    ),
}


def resolve_policy(
    placement: Placement,
    *,
    limit: int | None = None,
    exclude_viewed: bool | None = None,
    exclude_purchased: bool | None = None,
    exclude_out_of_stock: bool | None = None,
) -> PlacementPolicy:
    """placement 기본 정책에 요청 override 를 합성한다."""
    base = _PLACEMENT_DEFAULTS[placement]
    return PlacementPolicy(
        placement=placement,
        limit=base.limit if limit is None else limit,
        exclude_viewed=base.exclude_viewed if exclude_viewed is None else exclude_viewed,
        exclude_purchased=(
            base.exclude_purchased if exclude_purchased is None else exclude_purchased
        ),
        exclude_out_of_stock=(
            base.exclude_out_of_stock
            if exclude_out_of_stock is None
            else exclude_out_of_stock
        ),
    )


@dataclass(frozen=True)
class ProductStat:
    """상품 단위 raw feature 입력."""

    product_id: str
    category: str | None
    view_count: int
    cart_add_count: int
    purchase_count: int
    buyer_count: int
    price: float | None = None
    original_price: float | None = None
    gross_margin: float | None = None
    rating: float | None = None
    review_count: int | None = None
    return_rate: float | None = None
    category_avg_price: float | None = None
    in_stock: bool = True


@dataclass(frozen=True)
class VisitorContext:
    """visitor 개인화 feature 입력."""

    visitor_id: str
    viewed_product_ids: frozenset[str]
    purchased_product_ids: frozenset[str]
    engaged_categories: frozenset[str]


@dataclass(frozen=True)
class Candidate:
    """candidate generation 결과."""

    product_id: str
    category: str | None
    stat: ProductStat


@dataclass(frozen=True)
class RecommendationFeatures:
    """ML ranker 입력 feature vector.

    모든 값은 0..1 범위로 정규화한다. 이 구조가 feature contract 이며,
    coefficient 학습/교체 시에도 HTTP API와 분리된다.
    """

    product_id: str
    category: str | None
    view_signal: float
    cart_signal: float
    purchase_signal: float
    buyer_signal: float
    category_affinity: float
    previously_viewed: float
    placement_message: float
    placement_onsite: float
    relative_value_signal: float
    discount_signal: float
    rating_signal: float
    review_confidence: float
    return_quality_signal: float
    margin_signal: float


@dataclass(frozen=True)
class ModelScore:
    product_id: str
    category: str | None
    probability: float
    reason: str


@dataclass(frozen=True)
class RecommendationModelArtifact:
    """Versioned trained model artifact loaded by the serving adapter."""

    model_version: str
    feature_version: str
    model_type: str
    bias: float
    weights: dict[str, float]
    metrics: dict[str, float]
    training_data: dict[str, str | int | float]


@dataclass(frozen=True)
class Recommendation:
    """ranking 결과."""

    product_id: str
    category: str | None
    score: float
    reason: str


@dataclass(frozen=True)
class RecommendationMetadata:
    model_version: str
    feature_version: str
    generated_at: datetime


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))
