"""온사이트 캠페인 seam 의 기본 어댑터.

운영 Kotlin campaign 서비스 연동 전까지 현재의 규칙 기반 캠페인/대상자 동작을
유지하는 fallback 구현이다. CampaignProvider/AudienceMembership 포트를 만족한다.
"""

from __future__ import annotations

from app.onsite.domain.campaign import ActiveCampaign
from app.onsite.domain.model import OnsiteCreative

_PRIORITY = {
    "cart_recovery": "high",
    "exit_intent": "high",
    "browse_assist": "medium",
}


def _creative(trigger: str, subject: str) -> OnsiteCreative:
    if trigger == "cart_recovery":
        return OnsiteCreative(
            headline="장바구니 상품을 이어서 확인해보세요",
            body="방금 담은 상품과 함께 구매하기 좋은 추천을 준비했습니다.",
            call_to_action="장바구니로 돌아가기",
        )
    if trigger == "exit_intent":
        return OnsiteCreative(
            headline="나가기 전에 맞춤 추천을 확인해보세요",
            body=f"{subject} 중심으로 지금 관심사에 맞는 상품을 골랐습니다.",
            call_to_action="추천 보기",
        )
    return OnsiteCreative(
        headline="보고 계신 상품과 어울리는 추천",
        body=f"{subject}와 함께 많이 비교되는 상품을 확인해보세요.",
        call_to_action="추천 보기",
    )


class DefaultCampaignProvider:
    """trigger 기반 규칙 캠페인(현 동작). Kotlin 연동 시 이 포트를 교체한다."""

    def __init__(self, holdout_ratio: float = 0.0) -> None:
        self._holdout_ratio = holdout_ratio

    def active_campaign(
        self, tenant_id: str, trigger: str, subject: str
    ) -> ActiveCampaign | None:
        _ = tenant_id
        return ActiveCampaign(
            campaign_id=f"onsite-{trigger}-v1",
            priority=_PRIORITY.get(trigger, "low"),
            creative=_creative(trigger, subject),
            experiment_enabled=self._holdout_ratio > 0,
            holdout_ratio=self._holdout_ratio,
        )


class AllowAllAudienceMembership:
    """기본 대상자 정책: 모든 방문자 허용(현 동작). 운영은 materialized audience 연동."""

    async def is_member(
        self, tenant_id: str, visitor_id: str, campaign_id: str
    ) -> bool:
        _ = (tenant_id, visitor_id, campaign_id)
        return True
