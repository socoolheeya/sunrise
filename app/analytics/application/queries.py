"""읽기 유스케이스 (CQRS Read). Port 에만 의존."""

from __future__ import annotations

from datetime import datetime

from app.analytics.domain.model import (
    BenchmarkReport,
    CohortReport,
    DashboardMetrics,
    Funnel,
    FunnelStep,
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
