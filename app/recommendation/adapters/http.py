"""Recommendation HTTP router (Inbound Adapter)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.config import Settings, get_settings
from app.core.tenant import require_tenant
from app.events.registry import RECOMMENDATION_RESPONSE_SCHEMA_VERSION
from app.recommendation.adapters.clickhouse import ClickHouseRecommendationRepository
from app.recommendation.adapters.model_registry import load_recommendation_model
from app.recommendation.adapters.repository import SqlRecommendationRepository
from app.recommendation.application.recommend import RecommendItems
from app.recommendation.domain.model import (
    Placement,
    RecommendationMetadata,
    RecommendationModelArtifact,
    resolve_policy,
)
from app.recommendation.domain.repository import RecommendationRepository

router = APIRouter(prefix="/v1/recommendations", tags=["recommendations"])


class RecommendationRequest(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=128)
    placement: Literal["widget", "message", "onsite"] = "widget"
    # placement 기본 정책을 덮어쓰는 선택적 override 들.
    limit: int | None = Field(default=None, ge=1, le=100)
    exclude_viewed: bool | None = None
    exclude_purchased: bool | None = None
    exclude_out_of_stock: bool | None = None
    # 품절 상품 id 목록(재고는 caller 가 가장 잘 안다 → caller 가 제공).
    out_of_stock: list[str] | None = Field(default=None, max_length=5000)


class ProductFeatureIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=128)
    price: float | None = Field(default=None, ge=0)
    original_price: float | None = Field(default=None, ge=0)
    gross_margin: float | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    return_rate: float | None = Field(default=None, ge=0, le=1)
    in_stock: bool = True


class ProductFeatureBatchRequest(BaseModel):
    products: list[ProductFeatureIn] = Field(min_length=1, max_length=1000)


class ProductFeatureBatchResponse(BaseModel):
    accepted: int


class RecommendationMetadataResponse(BaseModel):
    model_version: str
    feature_version: str
    generated_at: datetime


class RecommendationItemResponse(BaseModel):
    product_id: str
    category: str | None
    score: float
    reason: str


class RecommendationResponse(BaseModel):
    schema_version: str = RECOMMENDATION_RESPONSE_SCHEMA_VERSION
    placement: str
    metadata: RecommendationMetadataResponse
    items: list[RecommendationItemResponse]


def get_recommendation_repo(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RecommendationRepository:
    settings = get_settings()
    if settings.analytics_backend == "clickhouse":
        client = getattr(request.app.state, "clickhouse_client", None)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ClickHouse client is not ready",
            )
        return ClickHouseRecommendationRepository(
            client,
            settings.clickhouse_events_table,
            product_stats_table=settings.clickhouse_product_stats_table,
            product_features_table=settings.clickhouse_product_features_table,
            visitor_product_signals_table=(
                settings.clickhouse_visitor_product_signals_table
            ),
        )
    return SqlRecommendationRepository(session)


def get_recommendation_model(
    settings: Settings = Depends(get_settings),
) -> RecommendationModelArtifact:
    return load_recommendation_model(settings.recommendation_model_path)


def _default_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=90)
    return start, end


def _metadata_response(
    metadata: RecommendationMetadata,
) -> RecommendationMetadataResponse:
    return RecommendationMetadataResponse(
        model_version=metadata.model_version,
        feature_version=metadata.feature_version,
        generated_at=metadata.generated_at,
    )


@router.post("/items", response_model=RecommendationResponse)
async def recommend_items(
    payload: RecommendationRequest,
    tenant_id: str = Depends(require_tenant),
    repo: RecommendationRepository = Depends(get_recommendation_repo),
    model_artifact: RecommendationModelArtifact = Depends(get_recommendation_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> RecommendationResponse:
    start, end = _default_window(start, end)
    placement = Placement(payload.placement)
    policy = resolve_policy(
        placement,
        limit=payload.limit,
        exclude_viewed=payload.exclude_viewed,
        exclude_purchased=payload.exclude_purchased,
        exclude_out_of_stock=payload.exclude_out_of_stock,
    )
    out_of_stock = frozenset(payload.out_of_stock or [])

    metadata, items = await RecommendItems(repo, model_artifact).execute(
        tenant_id, payload.visitor_id, policy, out_of_stock, start, end
    )
    return RecommendationResponse(
        schema_version=RECOMMENDATION_RESPONSE_SCHEMA_VERSION,
        placement=placement.value,
        metadata=_metadata_response(metadata),
        items=[
            RecommendationItemResponse(
                product_id=item.product_id,
                category=item.category,
                score=item.score,
                reason=item.reason,
            )
            for item in items
        ],
    )


@router.post("/products", response_model=ProductFeatureBatchResponse)
async def upsert_products(
    payload: ProductFeatureBatchRequest,
    tenant_id: str = Depends(require_tenant),
    repo: RecommendationRepository = Depends(get_recommendation_repo),
) -> ProductFeatureBatchResponse:
    accepted = await repo.upsert_product_features(
        tenant_id,
        [product.model_dump() for product in payload.products],
    )
    return ProductFeatureBatchResponse(accepted=accepted)
