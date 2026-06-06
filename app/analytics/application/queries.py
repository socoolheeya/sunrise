"""읽기 유스케이스 (CQRS Read). Port 에만 의존."""

from __future__ import annotations

from datetime import datetime

from app.analytics.domain.model import (
    BenchmarkReport,
    CohortReport,
    DataTalkReport,
    DashboardMetrics,
    Funnel,
    FunnelStep,
    InflowReport,
    LifecycleSegment,
    LifecycleSegmentReport,
    RevenueBreakdown,
    purchase_segment,
    visit_segment,
)
from app.analytics.domain.repository import AnalyticsRepository

# 퍼널 단계 정의: 조회 → 장바구니 → 구매.
_FUNNEL_STEPS: tuple[tuple[str, str], ...] = (
    ("조회", "view"),
    ("장바구니", "cart_add"),
    ("구매", "purchase"),
)


class GetDashboardMetrics:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> DashboardMetrics:
        inputs = await self._repository.metric_inputs(tenant_id, start, end)
        return DashboardMetrics.from_inputs(inputs)


class GetFunnel:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(self, tenant_id: str, start: datetime, end: datetime) -> Funnel:
        counts = await self._repository.funnel_visitor_counts(tenant_id, start, end)
        steps = tuple(
            FunnelStep(name=label, visitors=counts.get(event_type, 0))
            for label, event_type in _FUNNEL_STEPS
        )
        return Funnel(steps=steps)


class GetCohort:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> CohortReport:
        records = await self._repository.purchase_months(tenant_id, start, end)
        return CohortReport.from_purchases(records)


class GetBenchmark:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> BenchmarkReport:
        tenant = DashboardMetrics.from_inputs(
            await self._repository.metric_inputs(tenant_id, start, end)
        )
        platform = DashboardMetrics.from_inputs(
            await self._repository.platform_metric_inputs(start, end)
        )
        return BenchmarkReport.compare(tenant, platform)


class GetInflow:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> InflowReport:
        channels = await self._repository.inflow_channels(tenant_id, start, end)
        channels.sort(key=lambda item: item.revenue, reverse=True)
        return InflowReport(channels=tuple(channels))


class GetRevenueBreakdown:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        return await self._repository.revenue_breakdown(tenant_id, start, end)


class GetLifecycleSegments:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> LifecycleSegmentReport:
        inputs = await self._repository.lifecycle_inputs(tenant_id, start, end)
        segments = tuple(
            LifecycleSegment(
                visitor_id=item.visitor_id,
                visit_segment=visit_segment(end, item.last_seen_at),
                purchase_segment=purchase_segment(
                    end, item.purchase_count, item.last_purchase_at
                ),
                revenue=item.revenue,
            )
            for item in inputs
        )
        return LifecycleSegmentReport(segments=segments)


class GetDataTalk:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> DataTalkReport:
        metrics = await GetDashboardMetrics(self._repository).execute(
            tenant_id, start, end
        )
        funnel = await GetFunnel(self._repository).execute(tenant_id, start, end)
        revenue_breakdown = await GetRevenueBreakdown(self._repository).execute(
            tenant_id, start, end
        )
        inflow = await GetInflow(self._repository).execute(tenant_id, start, end)
        anomalies: list[str] = []
        if metrics.visitor_count == 0:
            anomalies.append("no_sessions")
        if metrics.visitor_count > 0 and metrics.cvr == 0:
            anomalies.append("no_conversion")
        if revenue_breakdown.total_revenue and revenue_breakdown.onsite_coverage_rate < 0.5:
            anomalies.append("low_onsite_tracking_coverage")
        return DataTalkReport(
            metrics=metrics,
            funnel=funnel,
            revenue_breakdown=revenue_breakdown,
            top_inflow_channels=inflow.channels[:5],
            anomalies=tuple(anomalies),
        )
