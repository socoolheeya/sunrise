"""Use cases for onsite campaign decisions."""

from __future__ import annotations

from uuid import uuid4

from app.onsite.domain.model import (
    OnsiteCreative,
    OnsiteDecision,
    OnsiteDecisionContext,
    OnsiteEventType,
    OnsitePlacement,
    OnsiteRecommendationItem,
)


def _priority(trigger: str) -> str:
    return {
        "cart_recovery": "high",
        "exit_intent": "high",
        "browse_assist": "medium",
    }.get(trigger, "low")


def _campaign_id(trigger: str) -> str:
    return f"onsite-{trigger}-v1"


def _creative(
    trigger: str,
    context: OnsiteDecisionContext,
    items: tuple[OnsiteRecommendationItem, ...],
) -> OnsiteCreative:
    subject = items[0].product_id if items else context.product_id or "recommended item"
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


class DecideOnsiteCampaign:
    """Pick the best onsite campaign trigger for the current visitor moment."""

    def execute(
        self,
        context: OnsiteDecisionContext,
        recommendations: tuple[OnsiteRecommendationItem, ...],
    ) -> OnsiteDecision:
        trigger = self._trigger(context)
        if trigger is None:
            return OnsiteDecision(
                decision_id=str(uuid4()),
                campaign_id=None,
                eligible=False,
                trigger=None,
                placement=context.placement,
                priority=None,
                creative=None,
                items=(),
                frequency_cap_key=f"{context.tenant_id}:{context.visitor_id}:none",
                generated_at=context.now,
            )

        return OnsiteDecision(
            decision_id=str(uuid4()),
            campaign_id=_campaign_id(trigger),
            eligible=True,
            trigger=trigger,
            placement=context.placement,
            priority=_priority(trigger),
            creative=_creative(trigger, context, recommendations),
            items=recommendations,
            frequency_cap_key=f"{context.tenant_id}:{context.visitor_id}:{trigger}",
            generated_at=context.now,
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
