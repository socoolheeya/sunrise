"""Audience template HTTP router."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audience.application.preview import (
    AudienceRuleEvaluator,
    resolve_rule,
    rule_hash,
    template_external_fields,
)
from app.audience.application.templates import (
    CATALOG_VERSION,
    GetAudienceTemplate,
    ListAudienceTemplates,
)
from app.audience.domain.model import AudienceTemplate
from app.core.cache import Cache, get_cache
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.tenant import require_tenant
from app.events.registry import AUDIENCE_RESPONSE_SCHEMA_VERSION
from app.prediction.adapters.model_registry import load_prediction_model

router = APIRouter(prefix="/v1/audiences", tags=["audiences"])


class AudienceTemplateResponse(BaseModel):
    template_id: str
    name: str
    category: str
    description: str
    rule: dict[str, Any]
    recommended_channels: list[str]
    recommended_trigger: str
    tags: list[str]
    # 이 배포에서 외부 소스(동의/쿠폰/배송 등)가 없어 평가 불가한 필드.
    external_fields: list[str] = []
    # 외부 필드 의존이 없어 현재 배포에서 실모수 평가가 가능한 템플릿인지.
    evaluable: bool = True


class AudienceTemplateListResponse(BaseModel):
    schema_version: str = AUDIENCE_RESPONSE_SCHEMA_VERSION
    catalog_version: str
    count: int
    templates: list[AudienceTemplateResponse]


class AudienceRuleRequest(BaseModel):
    template_id: str | None = None
    rule: dict[str, Any] | None = None
    sample_limit: int = Field(default=20, ge=1, le=200)


class AudienceMaterializeRequest(AudienceRuleRequest):
    audience_id: str = Field(min_length=1, max_length=128)


class AudiencePreviewResponse(BaseModel):
    schema_version: str = AUDIENCE_RESPONSE_SCHEMA_VERSION
    rule_hash: str
    matched_count: int
    sample_visitor_ids: list[str]
    unsupported_conditions: list[str]
    evaluated_at: datetime


class AudienceMaterializationResponse(BaseModel):
    schema_version: str = AUDIENCE_RESPONSE_SCHEMA_VERSION
    audience_id: str
    rule_hash: str
    member_count: int
    sample_visitor_ids: list[str]
    status: str
    as_of: datetime


def _default_window(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime, datetime]:
    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=90)
    return start, end


def _response(template: AudienceTemplate) -> AudienceTemplateResponse:
    external = template_external_fields(template.rule)
    return AudienceTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        category=template.category,
        description=template.description,
        rule=template.rule,
        recommended_channels=list(template.recommended_channels),
        recommended_trigger=template.recommended_trigger,
        tags=list(template.tags),
        external_fields=external,
        evaluable=not external,
    )


@router.get("/templates", response_model=AudienceTemplateListResponse)
async def list_templates(
    tenant_id: str = Depends(require_tenant),
    category: str | None = Query(default=None, max_length=64),
    query: str | None = Query(default=None, max_length=128),
) -> AudienceTemplateListResponse:
    _ = tenant_id
    templates = ListAudienceTemplates().execute(category=category, query=query)
    return AudienceTemplateListResponse(
        catalog_version=CATALOG_VERSION,
        count=len(templates),
        templates=[_response(template) for template in templates],
    )


@router.get("/templates/{template_id}", response_model=AudienceTemplateResponse)
async def get_template(
    template_id: str,
    tenant_id: str = Depends(require_tenant),
) -> AudienceTemplateResponse:
    _ = tenant_id
    template = GetAudienceTemplate().execute(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="audience template not found",
        )
    return _response(template)


@router.post("/preview", response_model=AudiencePreviewResponse)
async def preview_audience(
    payload: AudienceRuleRequest,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> AudiencePreviewResponse:
    start, end = _default_window(start, end)
    try:
        rule = resolve_rule(payload.template_id, payload.rule)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    model_artifact = load_prediction_model(settings.prediction_model_path)
    cache_key = (
        f"{AUDIENCE_RESPONSE_SCHEMA_VERSION}:"
        f"{tenant_id}:preview:{start.isoformat()}:{end.isoformat()}:"
        f"{payload.sample_limit}:{rule_hash(rule)}"
    )
    hit = await cache.get(cache_key)
    if hit is not None:
        return AudiencePreviewResponse.model_validate_json(hit)
    preview = await AudienceRuleEvaluator(session, model_artifact).preview(
        tenant_id,
        rule,
        start,
        end,
        sample_limit=payload.sample_limit,
    )
    response = AudiencePreviewResponse(
        rule_hash=preview.rule_hash,
        matched_count=preview.matched_count,
        sample_visitor_ids=list(preview.sample_visitor_ids),
        unsupported_conditions=list(preview.unsupported_conditions),
        evaluated_at=preview.evaluated_at,
    )
    await cache.set(cache_key, response.model_dump_json(), settings.cache_ttl_seconds)
    return response


@router.post("/materialize", response_model=AudienceMaterializationResponse)
async def materialize_audience(
    payload: AudienceMaterializeRequest,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> AudienceMaterializationResponse:
    start, end = _default_window(start, end)
    try:
        rule = resolve_rule(payload.template_id, payload.rule)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    materialization = await AudienceRuleEvaluator(
        session,
        load_prediction_model(settings.prediction_model_path),
    ).materialize(
        tenant_id,
        payload.audience_id,
        rule,
        start,
        end,
        sample_limit=payload.sample_limit,
    )
    return AudienceMaterializationResponse(
        audience_id=materialization.audience_id,
        rule_hash=materialization.rule_hash,
        member_count=materialization.member_count,
        sample_visitor_ids=list(materialization.sample_visitor_ids),
        status=materialization.status,
        as_of=materialization.as_of,
    )
