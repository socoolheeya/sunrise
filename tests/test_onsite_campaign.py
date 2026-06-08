"""온사이트 캠페인 seam(P2-10): CampaignProvider/AudienceMembership/experiment group."""

from __future__ import annotations

from datetime import datetime, timezone

from app.onsite.application.decide import DecideOnsiteCampaign
from app.onsite.domain.campaign import ActiveCampaign, assign_experiment_group
from app.onsite.domain.model import (
    OnsiteCreative,
    OnsiteDecisionContext,
    OnsiteEventType,
    OnsitePlacement,
    RecentBehavior,
)

NOW = datetime(2026, 6, 8, tzinfo=timezone.utc)


def _context() -> OnsiteDecisionContext:
    return OnsiteDecisionContext(
        tenant_id="tenant-a",
        visitor_id="v1",
        current_event=OnsiteEventType.VIEW,
        page_url="https://shop/p1",
        product_id="p1",
        category="cat",
        placement=OnsitePlacement.POPUP,
        recent=RecentBehavior(),
        now=NOW,
    )


_CREATIVE = OnsiteCreative(headline="h", body="b", call_to_action="c")


class _Provider:
    def __init__(self, campaign):
        self._campaign = campaign

    def active_campaign(self, tenant_id, trigger, subject):
        return self._campaign


class _Membership:
    def __init__(self, member):
        self._member = member

    async def is_member(self, tenant_id, visitor_id, campaign_id):
        return self._member


# ---- experiment group ----
def test_assign_experiment_group_deterministic_and_bounded():
    assert assign_experiment_group("t", "v", "c", 0.0) == "treatment"
    assert assign_experiment_group("t", "v", "c", 1.0) == "holdout"
    g1 = assign_experiment_group("t", "v", "c", 0.5)
    g2 = assign_experiment_group("t", "v", "c", 0.5)
    assert g1 == g2  # 결정론적


# ---- 결정 경로 ----
async def test_default_decision_is_treatment_and_eligible():
    decision = await DecideOnsiteCampaign().execute(_context(), ())
    assert decision.eligible is True
    assert decision.campaign_id == "onsite-browse_assist-v1"
    assert decision.experiment_group == "treatment"


async def test_no_active_campaign_is_ineligible():
    decide = DecideOnsiteCampaign(campaign_provider=_Provider(None))
    decision = await decide.execute(_context(), ())
    assert decision.eligible is False
    assert decision.campaign_id is None


async def test_non_member_is_ineligible_but_campaign_recorded():
    campaign = ActiveCampaign(campaign_id="c-1", priority="high", creative=_CREATIVE)
    decide = DecideOnsiteCampaign(
        campaign_provider=_Provider(campaign), membership=_Membership(False)
    )
    decision = await decide.execute(_context(), ())
    assert decision.eligible is False
    assert decision.campaign_id == "c-1"  # 대상자 아님(기록은 유지)


async def test_holdout_group_is_ineligible_but_measured():
    campaign = ActiveCampaign(
        campaign_id="c-1", priority="high", creative=_CREATIVE,
        experiment_enabled=True, holdout_ratio=1.0,
    )
    decide = DecideOnsiteCampaign(campaign_provider=_Provider(campaign))
    decision = await decide.execute(_context(), ())
    assert decision.eligible is False
    assert decision.experiment_group == "holdout"
    assert decision.campaign_id == "c-1"  # 측정 위해 기록
