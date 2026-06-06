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
    BuildVisitorPredictionFeatures,
    GetChurnRisks,
    GetCustomerLifetimeValues,
    GetProductAffinities,
    GetPurchaseScores,
    MultiHeadLogisticPredictionModel,
)
from app.prediction.domain.repository import PredictionRepository
from app.prediction.domain.model import PredictionModelArtifact, score_band

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


class ClvResponse(BaseModel):
    visitor_id: str
    survival_probability: float
    expected_purchases: float
    expected_order_value: float
    predicted_clv: float
    band: str
    reasons: list[str]


class ClvBatchResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    metadata: PredictionMetadataResponse
    horizon_days: int
    values: list[ClvResponse]


class ProductAffinityResponse(BaseModel):
    visitor_id: str
    key: str
    score: float


class ProductAffinityBatchResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    metadata: PredictionMetadataResponse
    affinities: list[ProductAffinityResponse]


class ModelStatusResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    model_version: str
    feature_version: str
    model_type: str
    heads: list[str]
    visitor_features: list[str]
    affinity_features: list[str]
    metrics: dict[str, float]
    training_data: dict[str, str | int | float]
    readiness: str
    drift_status: str
    loaded_at: datetime


class PredictionExplainRequest(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=128)
    target: str = Field(default="purchase_score", pattern="^(purchase_score|churn_risk)$")


class FeatureContributionResponse(BaseModel):
    feature: str
    value: float
    weight: float
    contribution: float


class PredictionExplainResponse(BaseModel):
    schema_version: str = PREDICTION_RESPONSE_SCHEMA_VERSION
    metadata: PredictionMetadataResponse
    visitor_id: str
    target: str
    score: float
    band: str
    bias: float
    logit: float
    contributions: list[FeatureContributionResponse]
    top_reasons: list[str]


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


def _readiness(model: PredictionModelArtifact) -> str:
    required_metrics = ("purchase_auc", "churn_auc", "affinity_auc")
    if all(model.metrics.get(metric, 0.0) >= 0.5 for metric in required_metrics):
        return "ready"
    return "degraded"


def _top_reason(feature: str, contribution: float, target: str) -> str:
    direction = "raises" if contribution >= 0 else "lowers"
    return f"{feature} {direction} {target}"


@router.get("/model-status", response_model=ModelStatusResponse)
async def model_status(
    model_artifact: PredictionModelArtifact = Depends(get_prediction_model),
) -> ModelStatusResponse:
    return ModelStatusResponse(
        model_version=model_artifact.model_version,
        feature_version=model_artifact.feature_version,
        model_type=model_artifact.model_type,
        heads=sorted(model_artifact.heads.keys()),
        visitor_features=list(model_artifact.visitor_features),
        affinity_features=list(model_artifact.affinity_features),
        metrics=dict(model_artifact.metrics),
        training_data=dict(model_artifact.training_data),
        readiness=_readiness(model_artifact),
        drift_status="not_configured",
        loaded_at=datetime.now(timezone.utc),
    )


@router.post("/explain", response_model=PredictionExplainResponse)
async def explain_prediction(
    payload: PredictionExplainRequest,
    tenant_id: str = Depends(require_tenant),
    repo: PredictionRepository = Depends(get_prediction_repo),
    model_artifact: PredictionModelArtifact = Depends(get_prediction_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> PredictionExplainResponse:
    start, end = _default_window(start, end)
    raw_features = await repo.visitor_features(
        tenant_id,
        [payload.visitor_id],
        start,
        end,
    )
    if not raw_features:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="visitor features not found",
        )
    features = BuildVisitorPredictionFeatures().execute(raw_features[0], end)
    model = MultiHeadLogisticPredictionModel(model_artifact)
    score = model.predict_visitor(payload.target, features)
    weights = model_artifact.heads[payload.target]
    bias = model_artifact.biases[payload.target]
    contributions = [
        FeatureContributionResponse(
            feature=name,
            value=round(float(getattr(features, name)), 6),
            weight=round(float(weight), 6),
            contribution=round(float(getattr(features, name) * weight), 6),
        )
        for name, weight in weights.items()
    ]
    contributions.sort(key=lambda item: abs(item.contribution), reverse=True)
    logit = round(bias + sum(item.contribution for item in contributions), 6)
    return PredictionExplainResponse(
        metadata=PredictionMetadataResponse(
            model_version=model_artifact.model_version,
            feature_version=model_artifact.feature_version,
            generated_at=end,
        ),
        visitor_id=payload.visitor_id,
        target=payload.target,
        score=score,
        band=score_band(score),
        bias=round(bias, 6),
        logit=logit,
        contributions=contributions,
        top_reasons=[
            _top_reason(item.feature, item.contribution, payload.target)
            for item in contributions[:3]
        ],
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


@router.post("/clv", response_model=ClvBatchResponse)
async def clv(
    payload: PredictionRequest,
    tenant_id: str = Depends(require_tenant),
    repo: PredictionRepository = Depends(get_prediction_repo),
    model_artifact: PredictionModelArtifact = Depends(get_prediction_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    horizon_days: int = Query(default=180, ge=1, le=1095),
) -> ClvBatchResponse:
    start, end = _default_window(start, end)
    metadata, values = await GetCustomerLifetimeValues(repo, model_artifact).execute(
        tenant_id, payload.visitor_ids, start, end, horizon_days
    )
    return ClvBatchResponse(
        schema_version=PREDICTION_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(metadata),
        horizon_days=horizon_days,
        values=[
            ClvResponse(
                visitor_id=value.visitor_id,
                survival_probability=value.survival_probability,
                expected_purchases=value.expected_purchases,
                expected_order_value=value.expected_order_value,
                predicted_clv=value.predicted_clv,
                band=value.band,
                reasons=list(value.reasons),
            )
            for value in values
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
