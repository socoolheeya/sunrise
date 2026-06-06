"""분석 도메인 Port. 구현은 adapters 계층."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.analytics.domain.model import (
    InflowChannel,
    MetricInputs,
    RevenueBreakdown,
    VisitorLifecycleInput,
)


class AnalyticsRepository(ABC):
    @abstractmethod
    async def metric_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> MetricInputs:
        """기간 내 KPI 원시 집계값."""
        raise NotImplementedError

    @abstractmethod
    async def funnel_visitor_counts(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> dict[str, int]:
        """이벤트 타입별 순방문자 수 (퍼널 구성용)."""
        raise NotImplementedError

    @abstractmethod
    async def purchase_months(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[tuple[str, str]]:
        """(visitor_id, 'YYYY-MM') 구매 기록 (코호트 분석용)."""
        raise NotImplementedError

    @abstractmethod
    async def platform_metric_inputs(
        self, start: datetime, end: datetime
    ) -> MetricInputs:
        """전체 테넌트(업종) 평균 산출용 집계값 (벤치마크용)."""
        raise NotImplementedError

    @abstractmethod
    async def inflow_channels(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[InflowChannel]:
        """유입 채널별 방문/구매/매출 집계."""
        raise NotImplementedError

    @abstractmethod
    async def revenue_breakdown(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        """총 매출, 온사이트 추적 매출, 기여 매출 집계."""
        raise NotImplementedError

    @abstractmethod
    async def lifecycle_inputs(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[VisitorLifecycleInput]:
        """방문/구매 세그먼트 산출용 방문자별 집계."""
        raise NotImplementedError
