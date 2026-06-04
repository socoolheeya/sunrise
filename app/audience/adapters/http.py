"""Audience template HTTP router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.audience.application.templates import (
    CATALOG_VERSION,
    GetAudienceTemplate,
    ListAudienceTemplates,
)
from app.audience.domain.model import AudienceTemplate
from app.core.tenant import require_tenant
from app.events.registry import AUDIENCE_RESPONSE_SCHEMA_VERSION

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


class AudienceTemplateListResponse(BaseModel):
    schema_version: str = AUDIENCE_RESPONSE_SCHEMA_VERSION
    catalog_version: str
    count: int
    templates: list[AudienceTemplateResponse]


def _response(template: AudienceTemplate) -> AudienceTemplateResponse:
    return AudienceTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        category=template.category,
        description=template.description,
        rule=template.rule,
        recommended_channels=list(template.recommended_channels),
        recommended_trigger=template.recommended_trigger,
        tags=list(template.tags),
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
