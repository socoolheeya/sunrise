"""모델 레지스트리 HTTP 라우터 (prediction/recommendation 공통).

버전 등록(staging) → promote(production, 무중단) → rollback(이전 버전 promote).
서빙은 테넌트 production 버전을 우선 사용한다(없으면 패키지 동봉 seed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.model_registry import ModelRegistryStore
from app.core.tenant import require_tenant
from app.prediction.adapters.model_registry import validate_prediction_artifact
from app.recommendation.adapters.model_registry import validate_recommendation_artifact

router = APIRouter(prefix="/v1/models", tags=["models"])

# model_name → artifact 검증기(등록 시 서빙 계약 위반 artifact 를 거부).
_VALIDATORS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "prediction": validate_prediction_artifact,
    "recommendation": validate_recommendation_artifact,
}


def _require_known_model(model_name: str) -> None:
    if model_name not in _VALIDATORS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown model: {model_name}",
        )


class RegisterVersionRequest(BaseModel):
    artifact: dict[str, Any] = Field(description="검증 대상 모델 artifact JSON")


class ModelVersionResponse(BaseModel):
    version: str
    status: str
    metrics: dict[str, Any]
    created_at: datetime
    promoted_at: datetime | None


class ModelVersionListResponse(BaseModel):
    model_name: str
    active_version: str | None  # DB production(없으면 None → 패키지 seed 사용)
    versions: list[ModelVersionResponse]


class PromoteRequest(BaseModel):
    version: str = Field(min_length=1, max_length=128)


@router.post("/{model_name}/versions", response_model=ModelVersionResponse)
async def register_version(
    model_name: str = Path(...),
    payload: RegisterVersionRequest = ...,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
) -> ModelVersionResponse:
    _require_known_model(model_name)
    try:
        _VALIDATORS[model_name](payload.artifact)
    except Exception as exc:  # 서빙 계약 위반 → 등록 거부
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid {model_name} artifact: {exc}",
        ) from exc
    version = str(payload.artifact.get("model_version") or "").strip()
    if not version:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="artifact.model_version is required",
        )
    metrics = payload.artifact.get("metrics") or {}
    store = ModelRegistryStore(session)
    await store.register(tenant_id, model_name, version, payload.artifact, metrics)
    listed = {v.version: v for v in await store.list_versions(tenant_id, model_name)}
    row = listed[version]
    return ModelVersionResponse(
        version=row.version, status=row.status, metrics=row.metrics,
        created_at=row.created_at, promoted_at=row.promoted_at,
    )


@router.post("/{model_name}/promote", response_model=ModelVersionListResponse)
async def promote_version(
    model_name: str = Path(...),
    payload: PromoteRequest = ...,
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
) -> ModelVersionListResponse:
    _require_known_model(model_name)
    store = ModelRegistryStore(session)
    if not await store.promote(tenant_id, model_name, payload.version):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"version not found: {payload.version}",
        )
    return await _list(store, tenant_id, model_name)


@router.get("/{model_name}", response_model=ModelVersionListResponse)
async def list_model_versions(
    model_name: str = Path(...),
    tenant_id: str = Depends(require_tenant),
    session: AsyncSession = Depends(get_session),
) -> ModelVersionListResponse:
    _require_known_model(model_name)
    return await _list(ModelRegistryStore(session), tenant_id, model_name)


async def _list(
    store: ModelRegistryStore, tenant_id: str, model_name: str
) -> ModelVersionListResponse:
    versions = await store.list_versions(tenant_id, model_name)
    active = next((v.version for v in versions if v.status == "production"), None)
    return ModelVersionListResponse(
        model_name=model_name,
        active_version=active,
        versions=[
            ModelVersionResponse(
                version=v.version, status=v.status, metrics=v.metrics,
                created_at=v.created_at, promoted_at=v.promoted_at,
            )
            for v in versions
        ],
    )
