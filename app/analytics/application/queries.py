"""읽기 유스케이스 (CQRS Read). Port 에만 의존."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from app.analytics.domain.model import (
    AttributionReport,
    BenchmarkReport,
    CohortReport,
    DataTalkReport,
    DataTalkSnapshot,
    DashboardMetrics,
    Funnel,
    FunnelStep,
    InflowReport,
    LifecycleSegment,
    LifecycleSegmentReport,
    RevenueBreakdown,
    SegmentTransitionReport,
    compute_segment_transitions,
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


class RefreshOrderFacts:
    """raw 이벤트를 order_fact 주문 원장으로 머티리얼라이즈하는 배치 유스케이스."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int = 24,
    ) -> int:
        return await self._repository.refresh_order_facts(
            tenant_id, start, end, attribution_window_hours
        )


class GetOrderRevenueBreakdown:
    """order_fact 원장 기반 매출 breakdown(주문 단위 중복제거)."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        return await self._repository.order_revenue_breakdown(tenant_id, start, end)


class RefreshCohortRetention:
    """기간 이벤트로 cohort_retention read model 을 머티리얼라이즈하는 배치 유스케이스."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        cohort_type: str,
        granularity: str,
        max_offset: int = 11,
    ) -> int:
        return await self._repository.refresh_cohort_retention(
            tenant_id, start, end, cohort_type, granularity, max_offset
        )


class GetCohortRetention:
    """머티리얼라이즈된 cohort retention 매트릭스 조회."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, cohort_type: str, granularity: str
    ) -> CohortReport:
        return await self._repository.cohort_retention(
            tenant_id, cohort_type, granularity
        )


class RefreshLifecycleSegments:
    """기간 집계로 as_of 시점 세그먼트 스냅샷을 머티리얼라이즈하는 배치 유스케이스."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime, as_of: datetime
    ) -> int:
        return await self._repository.refresh_lifecycle_segments(
            tenant_id, start, end, as_of
        )


class GetSegmentTransitions:
    """두 스냅샷(as_of_from, as_of_to)을 비교해 세그먼트 이동을 산출."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        tenant_id: str,
        as_of_from: datetime,
        as_of_to: datetime,
        segment_type: str,
    ) -> SegmentTransitionReport:
        previous = await self._repository.segment_snapshot(tenant_id, as_of_from)
        current = await self._repository.segment_snapshot(tenant_id, as_of_to)
        return compute_segment_transitions(previous, current, segment_type)


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


class GetAttribution:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> AttributionReport:
        channels = await self._repository.attribution_channels(
            tenant_id,
            start,
            end,
            attribution_window_hours,
        )
        channels.sort(key=lambda item: item.revenue, reverse=True)
        return AttributionReport(channels=tuple(channels))


class CreateDataTalkSnapshot:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> DataTalkSnapshot:
        report = await GetDataTalk(self._repository).execute(tenant_id, start, end)
        generated_at = datetime.now(timezone.utc)
        snapshot_id = sha256(
            f"{tenant_id}:{start.isoformat()}:{end.isoformat()}".encode("utf-8")
        ).hexdigest()[:24]
        snapshot = DataTalkSnapshot(
            snapshot_id=snapshot_id,
            status="frozen",
            report=report,
            generated_at=generated_at,
        )
        await self._repository.save_datatalk_snapshot(tenant_id, start, end, snapshot)
        return snapshot
