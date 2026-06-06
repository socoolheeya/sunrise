"""분석/지표 도메인 모델 (순수).

KPI 계산식(CVR/AOV/재구매율)과 퍼널 이탈률을 도메인에 둔다.
저장소는 원시 집계값만 제공하고, 비율 계산은 여기서 수행한다 → 인프라 없이 테스트 가능.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MetricInputs:
    """저장소가 산출하는 원시 집계값."""

    visitor_count: int  # 기간 내 순방문자(distinct visitor)
    purchaser_count: int  # 구매한 순방문자
    purchase_count: int  # 구매 건수
    revenue: float  # 매출 합계
    repeat_purchaser_count: int  # 2회 이상 구매한 방문자
    session_count: int = 0  # 기간 내 순세션. 세션 ID가 없으면 visitor_count로 대체.


@dataclass(frozen=True)
class DashboardMetrics:
    revenue: float
    session_count: int
    visitor_count: int
    purchase_count: int
    cvr: float  # 전환율 = 구매자 / 방문자
    aov: float  # 객단가 = 매출 / 구매건수
    repeat_rate: float  # 재구매율 = 재구매자 / 구매자

    @classmethod
    def from_inputs(cls, m: MetricInputs) -> "DashboardMetrics":
        cvr = m.purchaser_count / m.visitor_count if m.visitor_count else 0.0
        aov = m.revenue / m.purchase_count if m.purchase_count else 0.0
        repeat = (
            m.repeat_purchaser_count / m.purchaser_count if m.purchaser_count else 0.0
        )
        return cls(
            revenue=m.revenue,
            session_count=m.session_count or m.visitor_count,
            visitor_count=m.visitor_count,
            purchase_count=m.purchase_count,
            cvr=cvr,
            aov=aov,
            repeat_rate=repeat,
        )


@dataclass(frozen=True)
class FunnelStep:
    name: str
    visitors: int


@dataclass(frozen=True)
class Funnel:
    steps: tuple[FunnelStep, ...]

    def drop_off(self, index: int) -> float:
        """index 단계의 직전 단계 대비 이탈률."""
        if index <= 0 or index >= len(self.steps):
            return 0.0
        prev = self.steps[index - 1].visitors
        cur = self.steps[index].visitors
        return 0.0 if prev == 0 else (prev - cur) / prev

    def overall_conversion(self) -> float:
        """첫 단계 대비 마지막 단계 전환율."""
        if not self.steps or self.steps[0].visitors == 0:
            return 0.0
        return self.steps[-1].visitors / self.steps[0].visitors


# ---- 코호트 분석 ----
def _month_offset(base: str, other: str) -> int:
    """'YYYY-MM' 두 값 사이의 개월 수 차이."""
    by, bm = (int(x) for x in base.split("-"))
    oy, om = (int(x) for x in other.split("-"))
    return (oy - by) * 12 + (om - bm)


@dataclass(frozen=True)
class CohortCell:
    offset: int  # 코호트 기준 경과 개월(0 = 첫 구매 달)
    active: int  # 해당 시점 재구매 방문자 수
    rate: float  # active / cohort size


@dataclass(frozen=True)
class CohortRow:
    cohort: str  # 첫 구매 월 'YYYY-MM'
    size: int
    cells: tuple[CohortCell, ...]


@dataclass(frozen=True)
class CohortReport:
    rows: tuple[CohortRow, ...]

    @staticmethod
    def from_purchases(records: list[tuple[str, str]]) -> "CohortReport":
        """(visitor_id, 'YYYY-MM') 구매 기록으로 리텐션 매트릭스를 구성한다."""
        # 방문자별 구매 월 집합.
        by_visitor: dict[str, set[str]] = {}
        for visitor_id, period in records:
            by_visitor.setdefault(visitor_id, set()).add(period)

        # 코호트(첫 구매 월) → 방문자별 오프셋 집합.
        cohorts: dict[str, list[set[int]]] = {}
        for months in by_visitor.values():
            first = min(months)
            offsets = {_month_offset(first, m) for m in months}
            cohorts.setdefault(first, []).append(offsets)

        rows: list[CohortRow] = []
        for cohort in sorted(cohorts):
            visitors = cohorts[cohort]
            size = len(visitors)
            max_offset = max((max(o) for o in visitors), default=0)
            cells = tuple(
                CohortCell(
                    offset=k,
                    active=(active := sum(1 for o in visitors if k in o)),
                    rate=active / size if size else 0.0,
                )
                for k in range(max_offset + 1)
            )
            rows.append(CohortRow(cohort=cohort, size=size, cells=cells))
        return CohortReport(rows=tuple(rows))


# ---- 벤치마크 ----
@dataclass(frozen=True)
class BenchmarkMetric:
    name: str
    tenant: float  # 우리 쇼핑몰 값
    benchmark: float  # 플랫폼(업종) 평균
    delta_ratio: float  # (tenant - benchmark) / benchmark


@dataclass(frozen=True)
class BenchmarkReport:
    metrics: tuple[BenchmarkMetric, ...]

    @staticmethod
    def _metric(name: str, tenant: float, benchmark: float) -> BenchmarkMetric:
        delta = (tenant - benchmark) / benchmark if benchmark else 0.0
        return BenchmarkMetric(
            name=name, tenant=tenant, benchmark=benchmark, delta_ratio=delta
        )

    @classmethod
    def compare(
        cls, tenant: DashboardMetrics, platform: DashboardMetrics
    ) -> "BenchmarkReport":
        return cls(
            metrics=(
                cls._metric("cvr", tenant.cvr, platform.cvr),
                cls._metric("aov", tenant.aov, platform.aov),
                cls._metric("repeat_rate", tenant.repeat_rate, platform.repeat_rate),
            )
        )


# ---- 유입/어트리뷰션 lite read model ----
@dataclass(frozen=True)
class InflowChannel:
    channel: str
    session_count: int
    visitor_count: int
    purchaser_count: int
    purchase_count: int
    revenue: float

    @property
    def cvr(self) -> float:
        return self.purchaser_count / self.visitor_count if self.visitor_count else 0.0

    @property
    def aov(self) -> float:
        return self.revenue / self.purchase_count if self.purchase_count else 0.0


@dataclass(frozen=True)
class InflowReport:
    channels: tuple[InflowChannel, ...]


@dataclass(frozen=True)
class RevenueBreakdown:
    total_revenue: float
    onsite_revenue: float
    attributed_revenue: float

    @property
    def hidden_revenue(self) -> float:
        return max(0.0, self.total_revenue - self.onsite_revenue)

    @property
    def onsite_coverage_rate(self) -> float:
        return self.onsite_revenue / self.total_revenue if self.total_revenue else 0.0


# ---- 방문/구매 lifecycle segment ----
@dataclass(frozen=True)
class VisitorLifecycleInput:
    visitor_id: str
    view_count: int
    purchase_count: int
    revenue: float
    last_seen_at: datetime | None
    last_purchase_at: datetime | None


@dataclass(frozen=True)
class LifecycleSegment:
    visitor_id: str
    visit_segment: str
    purchase_segment: str
    revenue: float


@dataclass(frozen=True)
class LifecycleSegmentReport:
    segments: tuple[LifecycleSegment, ...]


def _days_since(reference: datetime, value: datetime | None) -> int:
    if value is None:
        return 365
    if reference.tzinfo is not None and value.tzinfo is None:
        value = value.replace(tzinfo=reference.tzinfo)
    if reference.tzinfo is None and value.tzinfo is not None:
        reference = reference.replace(tzinfo=value.tzinfo)
    return max(0, (reference - value).days)


def visit_segment(reference: datetime, last_seen_at: datetime | None) -> str:
    days = _days_since(reference, last_seen_at)
    if days <= 7:
        return "visit_active"
    if days <= 30:
        return "visit_risk"
    return "visit_inactive"


def purchase_segment(
    reference: datetime, purchase_count: int, last_purchase_at: datetime | None
) -> str:
    if purchase_count <= 0:
        return "no_purchase"
    days = _days_since(reference, last_purchase_at)
    if days <= 30:
        return "purchase_active"
    if days <= 90:
        return "purchase_risk"
    return "purchase_inactive"


# ---- DataTalk daily report snapshot ----
@dataclass(frozen=True)
class DataTalkReport:
    metrics: DashboardMetrics
    funnel: Funnel
    revenue_breakdown: RevenueBreakdown
    top_inflow_channels: tuple[InflowChannel, ...]
    anomalies: tuple[str, ...]
