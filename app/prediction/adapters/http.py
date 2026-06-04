"""Prediction HTTP router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.tenant import require_tenant
from app.events.registry import PREDICTION_RESPONSE_SCHEMA_VERSION
from app.prediction.adapters.model_registry import load_prediction_model
from app.prediction.adapters.clickhouse import ClickHousePredictionRepository
from app.prediction.adapters.repository import SqlPredictionRepository
from app.prediction.application.scoring import (
    GetChurnRisks,
    GetProductAffinities,
    GetPurchaseScores,
)
from app.prediction.domain.repository import PredictionRepository
from app.prediction.domain.model import PredictionModelArtifact

router = APIRouter(prefix="/v1/predictions", tags=["predictions"])


class PredictionRequest(BaseModel):
    visitor_ids: list[str] = Field(min_length=1, max_length=500)


class ProductAffinityRequest(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=128)
    keys: list[str] | None = Field(default=None, max_length=100)


class PredictionMetadataResponse(BaseModel):
    model_version: str
    feature_version: str
    generated_at: datetime


class PurchaseScoreResponse(BaseModel):
    visitor_id: str
    score: float
    band: str


class PurchaseScoreBatchResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    metadata: PredictionMetadataResponse
    scores: list[PurchaseScoreResponse]


class ChurnRiskResponse(BaseModel):
    visitor_id: str
    risk: float
    band: str
    recommended_retargeting_days: int


class ChurnRiskBatchResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    metadata: PredictionMetadataResponse
    risks: list[ChurnRiskResponse]


class ProductAffinityResponse(BaseModel):
    visitor_id: str
    key: str
    score: float


class ProductAffinityBatchResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    metadata: PredictionMetadataResponse
    affinities: list[ProductAffinityResponse]


def get_prediction_repo(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PredictionRepository:
    settings = get_settings()
    if settings.analytics_backend == "clickhouse":
        client = getattr(request.app.state, "clickhouse_client", None)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ClickHouse client is not ready",
            )
        return ClickHousePredictionRepository(
            client,
            settings.clickhouse_events_table,
            visitor_features_table=settings.clickhouse_visitor_features_table,
            visitor_product_signals_table=(
                settings.clickhouse_visitor_product_signals_table
            ),
        )
    return SqlPredictionRepository(session)


def get_prediction_model(
    settings: Settings = Depends(get_settings),
) -> PredictionModelArtifact:
    return load_prediction_model(settings.prediction_model_path)


def _default_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=90)
    return start, end


def _metadata_response(metadata) -> PredictionMetadataResponse:
    return PredictionMetadataResponse(
        model_version=metadata.model_version,
        feature_version=metadata.feature_version,
        generated_at=metadata.generated_at,
    )


@router.post("/purchase-score", response_model=PurchaseScoreBatchResponse)
async def purchase_score(
    payload: PredictionRequest,
    tenant_id: str = Depends(require_tenant),
    repo: PredictionRepository = Depends(get_prediction_repo),
    model_artifact: PredictionModelArtifact = Depends(get_prediction_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> PurchaseScoreBatchResponse:
    start, end = _default_window(start, end)
    metadata, scores = await GetPurchaseScores(repo, model_artifact).execute(
        tenant_id, payload.visitor_ids, start, end
    )
    return PurchaseScoreBatchResponse(
        schema_version=PREDICTION_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(metadata),
        scores=[
            PurchaseScoreResponse(
                visitor_id=score.visitor_id,
                score=score.score,
                band=score.band,
            )
            for score in scores
        ],
    )


@router.post("/churn-risk", response_model=ChurnRiskBatchResponse)
async def churn_risk(
    payload: PredictionRequest,
    tenant_id: str = Depends(require_tenant),
    repo: PredictionRepository = Depends(get_prediction_repo),
    model_artifact: PredictionModelArtifact = Depends(get_prediction_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> ChurnRiskBatchResponse:
    start, end = _default_window(start, end)
    metadata, risks = await GetChurnRisks(repo, model_artifact).execute(
        tenant_id, payload.visitor_ids, start, end
    )
    return ChurnRiskBatchResponse(
        schema_version=PREDICTION_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(metadata),
        risks=[
            ChurnRiskResponse(
                visitor_id=risk.visitor_id,
                risk=risk.risk,
                band=risk.band,
                recommended_retargeting_days=risk.recommended_retargeting_days,
            )
            for risk in risks
        ],
    )


@router.post("/product-affinity", response_model=ProductAffinityBatchResponse)
async def product_affinity(
    payload: ProductAffinityRequest,
    tenant_id: str = Depends(require_tenant),
    repo: PredictionRepository = Depends(get_prediction_repo),
    model_artifact: PredictionModelArtifact = Depends(get_prediction_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> ProductAffinityBatchResponse:
    start, end = _default_window(start, end)
    metadata, affinities = await GetProductAffinities(repo, model_artifact).execute(
        tenant_id, payload.visitor_id, payload.keys, start, end
    )
    return ProductAffinityBatchResponse(
        schema_version=PREDICTION_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(metadata),
        affinities=[
            ProductAffinityResponse(
                visitor_id=affinity.visitor_id,
                key=affinity.key,
                score=affinity.score,
            )
            for affinity in affinities
        ],
    )
