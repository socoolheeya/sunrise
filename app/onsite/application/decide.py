"""Use cases for onsite campaign decisions."""

from __future__ import annotations

from uuid import uuid4

from app.onsite.adapters.campaign import (
    AllowAllAudienceMembership,
    DefaultCampaignProvider,
)
from app.onsite.domain.campaign import (
    AudienceMembership,
    CampaignProvider,
    assign_experiment_group,
)
from app.onsite.domain.model import (
    OnsiteDecision,
    OnsiteDecisionContext,
    OnsiteEventType,
    OnsiteRecommendationItem,
)


class DecideOnsiteCampaign:
    """현재 방문자 맥락에 맞는 온사이트 캠페인을 결정한다.

    활성 캠페인/대상자/실험군은 포트(CampaignProvider/AudienceMembership)에서
    가져온다. 기본 어댑터는 현 규칙 동작을 유지(fallback)하며, 운영에서는 Kotlin
    campaign 서비스 어댑터로 교체한다.
    """

    def __init__(
        self,
        campaign_provider: CampaignProvider | None = None,
        membership: AudienceMembership | None = None,
    ) -> None:
        self._campaigns = campaign_provider or DefaultCampaignProvider()
        self._membership = membership or AllowAllAudienceMembership()

    def _ineligible(
        self, context: OnsiteDecisionContext, trigger: str | None, *,
        campaign_id: str | None = None, experiment_group: str | None = None,
    ) -> OnsiteDecision:
        suffix = trigger or "none"
        return OnsiteDecision(
            decision_id=str(uuid4()),
            campaign_id=campaign_id,
            eligible=False,
            trigger=trigger,
            placement=context.placement,
            priority=None,
            creative=None,
            items=(),
            frequency_cap_key=f"{context.tenant_id}:{context.visitor_id}:{suffix}",
            generated_at=context.now,
            experiment_group=experiment_group,
        )

    async def execute(
        self,
        context: OnsiteDecisionContext,
        recommendations: tuple[OnsiteRecommendationItem, ...],
    ) -> OnsiteDecision:
        trigger = self._trigger(context)
        if trigger is None:
            return self._ineligible(context, None)

        subject = (
            recommendations[0].product_id
            if recommendations
            else context.product_id or "recommended item"
        )
        campaign = self._campaigns.active_campaign(context.tenant_id, trigger, subject)
        if campaign is None:  # 활성 캠페인 없음 → 노출 안 함
            return self._ineligible(context, trigger)

        # 대상자(audience membership) 검증
        if not await self._membership.is_member(
            context.tenant_id, context.visitor_id, campaign.campaign_id
        ):
            return self._ineligible(context, trigger, campaign_id=campaign.campaign_id)

        # 실험군 배정: holdout 은 노출하지 않되 측정을 위해 기록.
        experiment_group = assign_experiment_group(
            context.tenant_id, context.visitor_id, campaign.campaign_id,
            campaign.holdout_ratio,
        )
        if experiment_group == "holdout":
            return self._ineligible(
                context, trigger,
                campaign_id=campaign.campaign_id, experiment_group=experiment_group,
            )

        return OnsiteDecision(
            decision_id=str(uuid4()),
            campaign_id=campaign.campaign_id,
            eligible=True,
            trigger=trigger,
            placement=context.placement,
            priority=campaign.priority,
            creative=campaign.creative,
            items=recommendations,
            frequency_cap_key=f"{context.tenant_id}:{context.visitor_id}:{trigger}",
            generated_at=context.now,
            experiment_group=experiment_group,
        )

    @staticmethod
    def _trigger(context: OnsiteDecisionContext) -> str | None:
        recent = context.recent
        if (
            context.current_event in {OnsiteEventType.EXIT_INTENT, OnsiteEventType.PAGE_HIDE}
            and recent.cart_product_ids
            and not recent.purchased_product_ids
        ):
            return "cart_recovery"
        if context.current_event == OnsiteEventType.EXIT_INTENT:
            return "exit_intent"
        if context.current_event in {OnsiteEventType.VIEW, OnsiteEventType.IDLE}:
            if context.product_id or recent.viewed_product_ids or context.category:
                return "browse_assist"
        return None
