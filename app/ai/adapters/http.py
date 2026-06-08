"""AI Agent/Copy HTTP router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.adapters.llm import create_llm_provider
from app.ai.adapters.review import AiReviewStore
from app.ai.application.agent import DiagnoseSite, GenerateCopy, SuggestCampaigns
from app.analytics.adapters.http import get_analytics_repo
from app.analytics.domain.repository import AnalyticsRepository
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.tenant import require_tenant
from app.events.registry import AI_RESPONSE_SCHEMA_VERSION

router = APIRouter(prefix="/v1/ai", tags=["ai"])


class SiteDiagnosisRequest(BaseModel):
    focus: Literal["site", "conversion", "retention"] = "site"


class AiMetadataResponse(BaseModel):
    model_version: str
    feature_version: str
    generated_at: datetime


class SiteIssueResponse(BaseModel):
    code: str
    severity: str
    segment: str
    summary: str
    evidence: str
    recommended_action: str


class SiteDiagnosisResponse(BaseModel):
    schema_version: str = AI_RESPONSE_SCHEMA_VERSION
    metadata: AiMetadataResponse
    health_score: float
    issues: list[SiteIssueResponse]


class CampaignSuggestionRequest(BaseModel):
    preferred_channels: list[Literal["kakao", "email", "sms", "onsite"]] = Field(
        default_factory=list, max_length=4
    )
    max_suggestions: int = Field(default=3, ge=1, le=10)


class CampaignSuggestionResponse(BaseModel):
    audience: str
    channel: str
    message_goal: str
    trigger: str
    rationale: str
    priority: str


class CampaignSuggestionsResponse(BaseModel):
    schema_version: str = AI_RESPONSE_SCHEMA_VERSION
    metadata: AiMetadataResponse
    suggestions: list[CampaignSuggestionResponse]


class CopyRequest(BaseModel):
    brand_tone: str = Field(min_length=1, max_length=120)
    campaign_goal: str = Field(min_length=1, max_length=160)
    product_name: str | None = Field(default=None, max_length=160)
    product_text: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=500)
    count: int = Field(default=3, ge=1, le=3)


class GuardrailResponse(BaseModel):
    passed: bool
    checks: list[str]
    reasons: list[str]


class CopyCandidateResponse(BaseModel):
    headline: str
    body: str
    call_to_action: str


class CopyResponse(BaseModel):
    schema_version: str = AI_RESPONSE_SCHEMA_VERSION
    metadata: AiMetadataResponse
    guardrail: GuardrailResponse
    requires_human_review: bool
    candidates: list[CopyCandidateResponse]
    # human review 가 필요하면 검토 큐에 적재되고 그 id 를 반환한다.
    review_id: int | None = None


class AiReviewResponse(BaseModel):
    id: int
    kind: str
    status: str
    payload: dict
    guardrail: dict
    reviewer: str | None
    note: str | None
    created_at: datetime
    decided_at: datetime | None


class AiReviewListResponse(BaseModel):
    schema_version: str = AI_RESPONSE_SCHEMA_VERSION
    reviews: list[AiReviewResponse]


class AiReviewDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=1000)


def _default_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    if end is None:
        now = datetime.now(timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        end = today + timedelta(days=1)
    if start is None:
        start = end - timedelta(days=30)
    return start, end


def _metadata_response(metadata) -> AiMetadataResponse:
    return AiMetadataResponse(
        model_version=metadata.model_version,
        feature_version=metadata.feature_version,
        generated_at=metadata.generated_at,
    )


@router.post("/diagnoses/site", response_model=SiteDiagnosisResponse)
async def diagnose_site(
    payload: SiteDiagnosisRequest | None = None,
    tenant_id: str = Depends(require_tenant),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> SiteDiagnosisResponse:
    _ = payload or SiteDiagnosisRequest()
    start, end = _default_window(start, end)
    diagnosis = await DiagnoseSite(repo).execute(tenant_id, start, end)
    return SiteDiagnosisResponse(
        schema_version=AI_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(diagnosis.metadata),
        health_score=diagnosis.health_score,
        issues=[
            SiteIssueResponse(
                code=issue.code,
                severity=issue.severity,
                segment=issue.segment,
                summary=issue.summary,
                evidence=issue.evidence,
                recommended_action=issue.recommended_action,
            )
            for issue in diagnosis.issues
        ],
    )


@router.post("/suggestions/campaigns", response_model=CampaignSuggestionsResponse)
async def suggest_campaigns(
    payload: CampaignSuggestionRequest,
    tenant_id: str = Depends(require_tenant),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> CampaignSuggestionsResponse:
    start, end = _default_window(start, end)
    result = await SuggestCampaigns(repo).execute(
        tenant_id,
        start,
        end,
        tuple(payload.preferred_channels),
        payload.max_suggestions,
    )
    return CampaignSuggestionsResponse(
        schema_version=AI_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(result.metadata),
        suggestions=[
            CampaignSuggestionResponse(
                audience=s.audience,
                channel=s.channel,
                message_goal=s.message_goal,
                trigger=s.trigger,
                rationale=s.rationale,
                priority=s.priority,
            )
            for s in result.suggestions
        ],
    )


@router.post("/copy", response_model=CopyResponse)
async def generate_copy(
    payload: CopyRequest,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CopyResponse:
    generated_at = datetime.now(timezone.utc)
    provider = create_llm_provider(settings)
    result = await GenerateCopy(provider).execute(
        brand_tone=payload.brand_tone,
        campaign_goal=payload.campaign_goal,
        product_name=payload.product_name,
        product_text=payload.product_text,
        image_url=payload.image_url,
        count=payload.count,
        generated_at=generated_at,
    )
    guardrail = GuardrailResponse(
        passed=result.guardrail.passed,
        checks=list(result.guardrail.checks),
        reasons=list(result.guardrail.reasons),
    )
    candidates = [
        CopyCandidateResponse(
            headline=c.headline, body=c.body, call_to_action=c.call_to_action
        )
        for c in result.candidates
    ]

    review_id: int | None = None
    if result.requires_human_review:
        review_id = await AiReviewStore(session).enqueue(
            tenant_id,
            kind="copy",
            payload={
                "request": payload.model_dump(),
                "candidates": [c.model_dump() for c in candidates],
                "model_version": result.metadata.model_version,
            },
            guardrail=guardrail.model_dump(),
        )

    return CopyResponse(
        schema_version=AI_RESPONSE_SCHEMA_VERSION,
        metadata=_metadata_response(result.metadata),
        guardrail=guardrail,
        requires_human_review=result.requires_human_review,
        candidates=candidates,
        review_id=review_id,
    )


@router.get("/reviews", response_model=AiReviewListResponse)
async def list_reviews(
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
    status_filter: Literal["pending", "approved", "rejected"] | None = Query(
        default="pending", alias="status"
    ),
) -> AiReviewListResponse:
    reviews = await AiReviewStore(session).list_reviews(tenant_id, status_filter)
    return AiReviewListResponse(
        reviews=[
            AiReviewResponse(
                id=r.id, kind=r.kind, status=r.status, payload=r.payload,
                guardrail=r.guardrail, reviewer=r.reviewer, note=r.note,
                created_at=r.created_at, decided_at=r.decided_at,
            )
            for r in reviews
        ]
    )


@router.post("/reviews/{review_id}/decision", response_model=AiReviewResponse)
async def decide_review(
    review_id: int,
    payload: AiReviewDecisionRequest,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
) -> AiReviewResponse:
    review = await AiReviewStore(session).decide(
        tenant_id, review_id, payload.decision,
        reviewer=payload.reviewer, note=payload.note,
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="review not found or not pending",
        )
    return AiReviewResponse(
        id=review.id, kind=review.kind, status=review.status, payload=review.payload,
        guardrail=review.guardrail, reviewer=review.reviewer, note=review.note,
        created_at=review.created_at, decided_at=review.decided_at,
    )
