"""Onsite campaign decision and interaction tracking HTTP router."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.core.tenant import require_tenant
from app.events.registry import TRACKING_EVENT_SCHEMA_VERSION
from app.ingestion.adapters.http import get_collect_use_case
from app.ingestion.application.collect_events import CollectEvents
from app.ingestion.domain.model import TrackingEvent
from app.onsite.application.decide import DecideOnsiteCampaign
from app.onsite.domain.model import (
    OnsiteDecisionContext,
    OnsiteEventType,
    OnsitePlacement,
    OnsiteRecommendationItem,
    RecentBehavior,
)
from app.recommendation.adapters.http import (
    get_recommendation_model,
    get_recommendation_repo,
)
from app.recommendation.application.recommend import RecommendItems
from app.recommendation.domain.model import Placement, RecommendationModelArtifact
from app.recommendation.domain.model import resolve_policy
from app.recommendation.domain.repository import RecommendationRepository

router = APIRouter(prefix="/v1/onsite", tags=["onsite"])


class RecentBehaviorIn(BaseModel):
    viewed_product_ids: list[str] = Field(default_factory=list, max_length=100)
    cart_product_ids: list[str] = Field(default_factory=list, max_length=100)
    purchased_product_ids: list[str] = Field(default_factory=list, max_length=100)
    last_event_at: datetime | None = None


class OnsiteDecisionRequest(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=128)
    current_event: Literal["view", "cart_add", "exit_intent", "idle", "page_hide"]
    page_url: str | None = Field(default=None, max_length=1000)
    product_id: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=128)
    placement: Literal["popup", "banner", "widget"] = "popup"
    recent: RecentBehaviorIn = Field(default_factory=RecentBehaviorIn)
    limit: int = Field(default=3, ge=1, le=10)


class OnsiteCreativeResponse(BaseModel):
    headline: str
    body: str
    call_to_action: str


class OnsiteItemResponse(BaseModel):
    product_id: str
    category: str | None
    score: float
    reason: str


class OnsiteDecisionResponse(BaseModel):
    schema_version: str = "onsite-decision.v1"
    decision_id: str
    campaign_id: str | None
    eligible: bool
    trigger: str | None
    placement: str
    priority: str | None
    creative: OnsiteCreativeResponse | None
    items: list[OnsiteItemResponse]
    frequency_cap_key: str
    generated_at: datetime


class OnsiteTrackRequest(BaseModel):
    decision_id: str = Field(min_length=1, max_length=128)
    campaign_id: str = Field(min_length=1, max_length=128)
    visitor_id: str = Field(min_length=1, max_length=128)
    product_id: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=128)
    occurred_at: datetime | None = None


class OnsiteTrackResponse(BaseModel):
    schema_version: str = TRACKING_EVENT_SCHEMA_VERSION
    accepted: int
    duplicates: int
    received_at: datetime


def _recent(payload: RecentBehaviorIn) -> RecentBehavior:
    return RecentBehavior(
        viewed_product_ids=tuple(payload.viewed_product_ids),
        cart_product_ids=tuple(payload.cart_product_ids),
        purchased_product_ids=tuple(payload.purchased_product_ids),
        last_event_at=payload.last_event_at,
    )


def _event_amount(event_name: str) -> float:
    return {
        "impressions": 1.0,
        "clicks": 1.0,
        "dismissals": 0.0,
    }[event_name]


def _event_type(event_name: str) -> str:
    return {
        "impressions": "campaign_impression",
        "clicks": "campaign_click",
        "dismissals": "campaign_dismiss",
    }[event_name]


@router.post("/decide", response_model=OnsiteDecisionResponse)
async def decide_onsite(
    payload: OnsiteDecisionRequest,
    tenant_id: str = Depends(require_tenant),
    repo: RecommendationRepository = Depends(get_recommendation_repo),
    model_artifact: RecommendationModelArtifact = Depends(get_recommendation_model),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> OnsiteDecisionResponse:
    now = datetime.now(timezone.utc)
    if end is None:
        end = now
    if start is None:
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    policy = resolve_policy(
        Placement.ONSITE,
        limit=payload.limit,
        exclude_purchased=True,
        exclude_out_of_stock=True,
    )
    _, recommended = await RecommendItems(repo, model_artifact).execute(
        tenant_id,
        payload.visitor_id,
        policy,
        frozenset(),
        start,
        end,
    )
    items = tuple(
        OnsiteRecommendationItem(
            product_id=item.product_id,
            category=item.category,
            score=item.score,
            reason=item.reason,
        )
        for item in recommended
    )
    decision = DecideOnsiteCampaign().execute(
        OnsiteDecisionContext(
            tenant_id=tenant_id,
            visitor_id=payload.visitor_id,
            current_event=OnsiteEventType(payload.current_event),
            page_url=payload.page_url,
            product_id=payload.product_id,
            category=payload.category,
            placement=OnsitePlacement(payload.placement),
            recent=_recent(payload.recent),
            now=now,
        ),
        items,
    )
    return OnsiteDecisionResponse(
        decision_id=decision.decision_id,
        campaign_id=decision.campaign_id,
        eligible=decision.eligible,
        trigger=decision.trigger,
        placement=decision.placement.value,
        priority=decision.priority,
        creative=(
            OnsiteCreativeResponse(
                headline=decision.creative.headline,
                body=decision.creative.body,
                call_to_action=decision.creative.call_to_action,
            )
            if decision.creative is not None
            else None
        ),
        items=[
            OnsiteItemResponse(
                product_id=item.product_id,
                category=item.category,
                score=item.score,
                reason=item.reason,
            )
            for item in decision.items
        ],
        frequency_cap_key=decision.frequency_cap_key,
        generated_at=decision.generated_at,
    )


async def _track(
    event_name: str,
    payload: OnsiteTrackRequest,
    tenant_id: str,
    use_case: CollectEvents,
) -> OnsiteTrackResponse:
    received_at = datetime.now(timezone.utc)
    occurred_at = payload.occurred_at or received_at
    event = TrackingEvent.create(
        tenant_id=tenant_id,
        event_id=f"onsite:{event_name}:{payload.decision_id}",
        visitor_id=payload.visitor_id,
        type=_event_type(event_name),
        occurred_at=occurred_at,
        received_at=received_at,
        product_id=payload.product_id,
        category=payload.category or payload.campaign_id,
        amount=_event_amount(event_name),
    )
    result = await use_case.execute([event])
    return OnsiteTrackResponse(
        accepted=result.accepted,
        duplicates=result.duplicates,
        received_at=received_at,
    )


@router.post(
    "/impressions",
    response_model=OnsiteTrackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def track_impression(
    payload: OnsiteTrackRequest,
    tenant_id: str = Depends(require_tenant),
    use_case: CollectEvents = Depends(get_collect_use_case),
) -> OnsiteTrackResponse:
    return await _track("impressions", payload, tenant_id, use_case)


@router.post(
    "/clicks",
    response_model=OnsiteTrackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def track_click(
    payload: OnsiteTrackRequest,
    tenant_id: str = Depends(require_tenant),
    use_case: CollectEvents = Depends(get_collect_use_case),
) -> OnsiteTrackResponse:
    return await _track("clicks", payload, tenant_id, use_case)


@router.post(
    "/dismissals",
    response_model=OnsiteTrackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def track_dismissal(
    payload: OnsiteTrackRequest,
    tenant_id: str = Depends(require_tenant),
    use_case: CollectEvents = Depends(get_collect_use_case),
) -> OnsiteTrackResponse:
    return await _track("dismissals", payload, tenant_id, use_case)
