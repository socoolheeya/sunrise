"""분석 도메인 Port. 구현은 adapters 계층."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.analytics.domain.model import (
    AttributionChannel,
    CohortReport,
    DataTalkSnapshot,
    InflowChannel,
    LifecycleSegment,
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

    @abstractmethod
    async def attribution_channels(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> list[AttributionChannel]:
        """캠페인 touchpoint 이후 구매 기여 매출을 채널별로 산출."""
        raise NotImplementedError

    @abstractmethod
    async def save_datatalk_snapshot(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        snapshot: DataTalkSnapshot,
    ) -> None:
        """DataTalk 리포트 snapshot 저장."""
        raise NotImplementedError

    @abstractmethod
    async def refresh_order_facts(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        attribution_window_hours: int,
    ) -> int:
        """기간 내 raw 이벤트를 order_fact read model 로 머티리얼라이즈하고 주문 수 반환."""
        raise NotImplementedError

    @abstractmethod
    async def order_revenue_breakdown(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> RevenueBreakdown:
        """order_fact 원장 기반 매출 breakdown(주문 단위 중복제거)."""
        raise NotImplementedError

    @abstractmethod
    async def refresh_lifecycle_segments(
        self, tenant_id: str, start: datetime, end: datetime, as_of: datetime
    ) -> int:
        """기간 집계로 as_of 시점 고객 세그먼트 스냅샷을 머티리얼라이즈하고 인원 반환."""
        raise NotImplementedError

    @abstractmethod
    async def segment_snapshot(
        self, tenant_id: str, as_of: datetime
    ) -> list[LifecycleSegment]:
        """as_of 시점 고객 세그먼트 스냅샷 조회."""
        raise NotImplementedError

    @abstractmethod
    async def refresh_cohort_retention(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        cohort_type: str,
        granularity: str,
        max_offset: int,
    ) -> int:
        """기간 이벤트로 cohort_retention read model 을 머티리얼라이즈하고 셀 수 반환."""
        raise NotImplementedError

    @abstractmethod
    async def cohort_retention(
        self, tenant_id: str, cohort_type: str, granularity: str
    ) -> CohortReport:
        """머티리얼라이즈된 cohort retention 매트릭스 조회."""
        raise NotImplementedError
