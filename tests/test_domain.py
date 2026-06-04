"""도메인 단위 테스트 (인프라 의존 0)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.analytics.domain.model import DashboardMetrics, Funnel, FunnelStep, MetricInputs
from app.ingestion.domain.model import TrackingEvent

NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


# ---- 수집 도메인 ----
def test_create_valid_event():
    e = TrackingEvent.create(
        tenant_id="t1",
        event_id="e1",
        visitor_id="v1",
        type="view",
        occurred_at=NOW,
        received_at=NOW,
    )
    assert e.tenant_id == "t1"


def test_purchase_requires_amount():
    with pytest.raises(ValueError):
        TrackingEvent.create(
            tenant_id="t1",
            event_id="e1",
            visitor_id="v1",
            type="purchase",
            occurred_at=NOW,
            received_at=NOW,
        )


def test_negative_amount_rejected():
    with pytest.raises(ValueError):
        TrackingEvent.create(
            tenant_id="t1",
            event_id="e1",
            visitor_id="v1",
            type="purchase",
            occurred_at=NOW,
            received_at=NOW,
            amount=-1,
        )


def test_missing_visitor_rejected():
    with pytest.raises(ValueError):
        TrackingEvent.create(
            tenant_id="t1",
            event_id="e1",
            visitor_id="",
            type="view",
            occurred_at=NOW,
            received_at=NOW,
        )


# ---- 분석 도메인 ----
def test_dashboard_metrics_calculation():
    m = DashboardMetrics.from_inputs(
        MetricInputs(
            visitor_count=4,
            purchaser_count=2,
            purchase_count=3,
            revenue=600.0,
            repeat_purchaser_count=1,
        )
    )
    assert m.cvr == pytest.approx(0.5)
    assert m.aov == pytest.approx(200.0)
    assert m.repeat_rate == pytest.approx(0.5)


def test_dashboard_metrics_zero_safe():
    m = DashboardMetrics.from_inputs(
        MetricInputs(
            visitor_count=0,
            purchaser_count=0,
            purchase_count=0,
            revenue=0.0,
            repeat_purchaser_count=0,
        )
    )
    assert m.cvr == 0.0
    assert m.aov == 0.0
    assert m.repeat_rate == 0.0


def test_funnel_drop_off_and_conversion():
    funnel = Funnel(
        steps=(
            FunnelStep("조회", 4),
            FunnelStep("장바구니", 3),
            FunnelStep("구매", 2),
        )
    )
    assert funnel.drop_off(0) == 0.0
    assert funnel.drop_off(1) == pytest.approx(0.25)
    assert funnel.drop_off(2) == pytest.approx(1 / 3)
    assert funnel.overall_conversion() == pytest.approx(0.5)
