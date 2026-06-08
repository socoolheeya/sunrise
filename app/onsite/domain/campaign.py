"""온사이트 캠페인 연동 seam (도메인 ports).

운영에서는 Kotlin campaign 서비스가 활성 캠페인·대상자(audience membership)·
실험군을 제공한다. 본 Python 결정 경로는 포트에만 의존하고, 기본 어댑터는 현재의
규칙 기반 동작을 유지(fallback)한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.onsite.domain.model import OnsiteCreative


@dataclass(frozen=True)
class ActiveCampaign:
    campaign_id: str
    priority: str
    creative: OnsiteCreative
    experiment_enabled: bool = False
    holdout_ratio: float = 0.0


class CampaignProvider(Protocol):
    """trigger 에 매칭되는 활성 캠페인을 반환(없으면 None → 노출 안 함)."""

    def active_campaign(
        self, tenant_id: str, trigger: str, subject: str
    ) -> ActiveCampaign | None: ...


class AudienceMembership(Protocol):
    """방문자가 해당 캠페인의 대상자(audience)인지 여부."""

    async def is_member(
        self, tenant_id: str, visitor_id: str, campaign_id: str
    ) -> bool: ...


def assign_experiment_group(
    tenant_id: str, visitor_id: str, campaign_id: str, holdout_ratio: float
) -> str:
    """결정론적 holdout 배정. 동일 (tenant,visitor,campaign) 은 항상 같은 군."""
    if holdout_ratio <= 0:
        return "treatment"
    digest = hashlib.sha256(
        f"{tenant_id}:{visitor_id}:{campaign_id}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < holdout_ratio else "treatment"
