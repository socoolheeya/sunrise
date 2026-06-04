"""테넌트 인증/격리.

X-Sunrise-Key 헤더의 API Key 를 tenant_id 로 해석한다.
모든 분석/수집 쿼리는 이 tenant_id 로 강제 스코프되어 멀티테넌트 격리를 보장한다.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import Settings, get_settings


async def require_tenant(
    x_sunrise_key: str | None = Header(default=None, alias="X-Sunrise-Key"),
) -> str:
    settings: Settings = get_settings()
    if not x_sunrise_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Sunrise-Key header required",
        )
    tenant_id = settings.api_keys.get(x_sunrise_key)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
        )
    return tenant_id
