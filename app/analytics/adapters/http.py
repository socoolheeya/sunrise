"""분석/지표 HTTP 라우터 (Inbound Adapter).

읽기 응답은 Cache 포트를 통해 get-or-compute 한다(Redis 또는 NullCache).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.domain.repository import AnalyticsRepository
from app.analytics.adapters.clickhouse import (
    ClickHouseAnalyticsRepository,
    create_clickhouse_client,
)
from app.analytics.adapters.repository import SqlAnalyticsRepository
from app.analytics.application.queries import (
    CreateDataTalkSnapshot,
    GetAttribution,
    GetBenchmark,
    GetCohort,
    GetDataTalk,
    GetDashboardMetrics,
    GetFunnel,
    GetInflow,
    GetLifecycleSegments,
    GetRevenueBreakdown,
)
from app.core.cache import Cache, get_cache
from app.core.clickhouse_migrations import apply_clickhouse_migrations
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.tenant import require_tenant
from app.events.registry import ANALYTICS_RESPONSE_SCHEMA_VERSION

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

TModel = TypeVar("TModel", bound=BaseModel)


# ---- 응답 스키마 (Presenter) ----
class DashboardResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    revenue: float
    session_count: int
    visitor_count: int
    purchase_count: int
    cvr: float
    aov: float
    repeat_rate: float


class FunnelStepResponse(BaseModel):
    name: str
    visitors: int
    drop_off: float


class FunnelResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    steps: list[FunnelStepResponse]
    overall_conversion: float


class CohortCellResponse(BaseModel):
    offset: int
    active: int
    rate: float


class CohortRowResponse(BaseModel):
    cohort: str
    size: int
    cells: list[CohortCellResponse]


class CohortResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    rows: list[CohortRowResponse]


class BenchmarkMetricResponse(BaseModel):
    name: str
    tenant: float
    benchmark: float
    delta_ratio: float


class BenchmarkResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    metrics: list[BenchmarkMetricResponse]


class InflowChannelResponse(BaseModel):
    channel: str
    session_count: int
    visitor_count: int
    purchaser_count: int
    purchase_count: int
    revenue: float
    cvr: float
    aov: float


class InflowResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    channels: list[InflowChannelResponse]


class RevenueBreakdownResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    total_revenue: float
    onsite_revenue: float
    hidden_revenue: float
    attributed_revenue: float
    onsite_coverage_rate: float


class AttributionChannelResponse(BaseModel):
    channel: str
    touchpoint_count: int
    purchaser_count: int
    purchase_count: int
    revenue: float
    cvr: float
    model: str


class AttributionResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    attribution_window_hours: int
    channels: list[AttributionChannelResponse]


class LifecycleSegmentResponse(BaseModel):
    visitor_id: str
    visit_segment: str
    purchase_segment: str
    revenue: float


class LifecycleSegmentReportResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    segments: list[LifecycleSegmentResponse]


class DataTalkFunnelStepResponse(BaseModel):
    name: str
    visitors: int


class DataTalkResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    start: datetime
    end: datetime
    metrics: DashboardResponse
    funnel: list[DataTalkFunnelStepResponse]
    revenue_breakdown: RevenueBreakdownResponse
    top_inflow_channels: list[InflowChannelResponse]
    anomalies: list[str]


class DataTalkSnapshotResponse(BaseModel):
    schema_version: str = ANALYTICS_RESPONSE_SCHEMA_VERSION
    snapshot_id: str
    status: str
    generated_at: datetime
    report: DataTalkResponse


# ---- 의존성 ----
def get_analytics_repo(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AnalyticsRepository:
    settings = get_settings()
    if settings.analytics_backend == "clickhouse":
        client = getattr(request.app.state, "clickhouse_client", None)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ClickHouse client is not ready",
            )
        return ClickHouseAnalyticsRepository(
            client,
            settings.clickhouse_events_table,
            metric_daily_table=settings.clickhouse_metric_daily_table,
        )
    return SqlAnalyticsRepository(session)


async def configure_analytics_backend(app, settings: Settings) -> None:
    if settings.analytics_backend == "clickhouse" or settings.clickhouse_mirror_ingestion:
        client = create_clickhouse_client(settings.clickhouse_dsn)
        if settings.analytics_backend == "clickhouse":
            await apply_clickhouse_migrations(client, settings)
        app.state.clickhouse_client = client


async def close_analytics_backend(app) -> None:
    client = getattr(app.state, "clickhouse_client", None)
    if client is None:
        return
    close = getattr(client, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result
    app.state.clickhouse_client = None


def _default_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    """기본 윈도우는 '오늘 포함 최근 30일'을 일(day) 경계로 정렬한다.

    - 일 경계 정렬 → 하루 동안 캐시 키가 안정적이라 캐시가 실제로 적중한다.
    - end 는 '내일 0시(UTC)'라 오늘 발생한 이벤트까지 포함한다.
    - 제품의 '지표 일 단위 갱신' 동작과도 일치한다.
    """
    if end is None:
        now = datetime.now(timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        end = today + timedelta(days=1)
    if start is None:
        start = end - timedelta(days=30)
    return start, end


def _cache_key(
    tenant_id: str,
    resource: str,
    start: datetime,
    end: datetime,
) -> str:
    return (
        f"{ANALYTICS_RESPONSE_SCHEMA_VERSION}:"
        f"{tenant_id}:{resource}:{start.isoformat()}:{end.isoformat()}"
    )


def _datatalk_response(
    start: datetime,
    end: datetime,
    report,
) -> DataTalkResponse:
    metrics = DashboardResponse(
        schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
        start=start,
        end=end,
        revenue=report.metrics.revenue,
        session_count=report.metrics.session_count,
        visitor_count=report.metrics.visitor_count,
        purchase_count=report.metrics.purchase_count,
        cvr=report.metrics.cvr,
        aov=report.metrics.aov,
        repeat_rate=report.metrics.repeat_rate,
    )
    revenue = report.revenue_breakdown
    revenue_response = RevenueBreakdownResponse(
        schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
        start=start,
        end=end,
        total_revenue=revenue.total_revenue,
        onsite_revenue=revenue.onsite_revenue,
        hidden_revenue=revenue.hidden_revenue,
        attributed_revenue=revenue.attributed_revenue,
        onsite_coverage_rate=revenue.onsite_coverage_rate,
    )
    return DataTalkResponse(
        schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
        start=start,
        end=end,
        metrics=metrics,
        funnel=[
            DataTalkFunnelStepResponse(name=step.name, visitors=step.visitors)
            for step in report.funnel.steps
        ],
        revenue_breakdown=revenue_response,
        top_inflow_channels=[
            InflowChannelResponse(
                channel=channel.channel,
                session_count=channel.session_count,
                visitor_count=channel.visitor_count,
                purchaser_count=channel.purchaser_count,
                purchase_count=channel.purchase_count,
                revenue=channel.revenue,
                cvr=channel.cvr,
                aov=channel.aov,
            )
            for channel in report.top_inflow_channels
        ],
        anomalies=list(report.anomalies),
    )


async def _cached(
    cache: Cache,
    key: str,
    ttl: int,
    model_cls: type[TModel],
    compute: Callable[[], Awaitable[TModel]],
) -> TModel:
    hit = await cache.get(key)
    if hit is not None:
        return model_cls.model_validate_json(hit)
    result = await compute()
    await cache.set(key, result.model_dump_json(), ttl)
    return result


# ---- 엔드포인트 ----
@router.get("/metrics", response_model=DashboardResponse)
async def dashboard(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> DashboardResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "metrics", start, end)

    async def compute() -> DashboardResponse:
        m = await GetDashboardMetrics(repo).execute(tenant_id, start, end)
        return DashboardResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            revenue=m.revenue,
            session_count=m.session_count,
            visitor_count=m.visitor_count,
            purchase_count=m.purchase_count,
            cvr=m.cvr,
            aov=m.aov,
            repeat_rate=m.repeat_rate,
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, DashboardResponse, compute
    )


@router.get("/funnel", response_model=FunnelResponse)
async def funnel(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> FunnelResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "funnel", start, end)

    async def compute() -> FunnelResponse:
        result = await GetFunnel(repo).execute(tenant_id, start, end)
        steps = [
            FunnelStepResponse(
                name=step.name, visitors=step.visitors, drop_off=result.drop_off(i)
            )
            for i, step in enumerate(result.steps)
        ]
        return FunnelResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            steps=steps,
            overall_conversion=result.overall_conversion(),
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, FunnelResponse, compute
    )


@router.get("/cohort", response_model=CohortResponse)
async def cohort(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> CohortResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "cohort", start, end)

    async def compute() -> CohortResponse:
        report = await GetCohort(repo).execute(tenant_id, start, end)
        rows = [
            CohortRowResponse(
                cohort=row.cohort,
                size=row.size,
                cells=[
                    CohortCellResponse(offset=c.offset, active=c.active, rate=c.rate)
                    for c in row.cells
                ],
            )
            for row in report.rows
        ]
        return CohortResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            rows=rows,
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, CohortResponse, compute
    )


@router.get("/benchmark", response_model=BenchmarkResponse)
async def benchmark(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> BenchmarkResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "benchmark", start, end)

    async def compute() -> BenchmarkResponse:
        report = await GetBenchmark(repo).execute(tenant_id, start, end)
        return BenchmarkResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            metrics=[
                BenchmarkMetricResponse(
                    name=m.name,
                    tenant=m.tenant,
                    benchmark=m.benchmark,
                    delta_ratio=m.delta_ratio,
                )
                for m in report.metrics
            ],
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, BenchmarkResponse, compute
    )


@router.get("/inflow", response_model=InflowResponse)
async def inflow(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> InflowResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "inflow", start, end)

    async def compute() -> InflowResponse:
        report = await GetInflow(repo).execute(tenant_id, start, end)
        return InflowResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            channels=[
                InflowChannelResponse(
                    channel=channel.channel,
                    session_count=channel.session_count,
                    visitor_count=channel.visitor_count,
                    purchaser_count=channel.purchaser_count,
                    purchase_count=channel.purchase_count,
                    revenue=channel.revenue,
                    cvr=channel.cvr,
                    aov=channel.aov,
                )
                for channel in report.channels
            ],
        )

    return await _cached(cache, key, settings.cache_ttl_seconds, InflowResponse, compute)


@router.get("/revenue-breakdown", response_model=RevenueBreakdownResponse)
async def revenue_breakdown(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> RevenueBreakdownResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "revenue-breakdown", start, end)

    async def compute() -> RevenueBreakdownResponse:
        report = await GetRevenueBreakdown(repo).execute(tenant_id, start, end)
        return RevenueBreakdownResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            total_revenue=report.total_revenue,
            onsite_revenue=report.onsite_revenue,
            hidden_revenue=report.hidden_revenue,
            attributed_revenue=report.attributed_revenue,
            onsite_coverage_rate=report.onsite_coverage_rate,
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, RevenueBreakdownResponse, compute
    )


@router.get("/attribution", response_model=AttributionResponse)
async def attribution(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    attribution_window_hours: int = Query(default=72, ge=1, le=720),
) -> AttributionResponse:
    start, end = _default_window(start, end)
    key = f"{_cache_key(tenant_id, 'attribution', start, end)}:{attribution_window_hours}"

    async def compute() -> AttributionResponse:
        report = await GetAttribution(repo).execute(
            tenant_id,
            start,
            end,
            attribution_window_hours,
        )
        return AttributionResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            attribution_window_hours=attribution_window_hours,
            channels=[
                AttributionChannelResponse(
                    channel=channel.channel,
                    touchpoint_count=channel.touchpoint_count,
                    purchaser_count=channel.purchaser_count,
                    purchase_count=channel.purchase_count,
                    revenue=channel.revenue,
                    cvr=channel.cvr,
                    model=channel.model,
                )
                for channel in report.channels
            ],
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, AttributionResponse, compute
    )


@router.get("/segments", response_model=LifecycleSegmentReportResponse)
async def segments(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> LifecycleSegmentReportResponse:
    start, end = _default_window(start, end)
    key = f"{_cache_key(tenant_id, 'segments', start, end)}:{limit}"

    async def compute() -> LifecycleSegmentReportResponse:
        report = await GetLifecycleSegments(repo).execute(tenant_id, start, end)
        ordered = sorted(
            report.segments,
            key=lambda segment: segment.revenue,
            reverse=True,
        )
        return LifecycleSegmentReportResponse(
            schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
            start=start,
            end=end,
            segments=[
                LifecycleSegmentResponse(
                    visitor_id=segment.visitor_id,
                    visit_segment=segment.visit_segment,
                    purchase_segment=segment.purchase_segment,
                    revenue=segment.revenue,
                )
                for segment in ordered[:limit]
            ],
        )

    return await _cached(
        cache, key, settings.cache_ttl_seconds, LifecycleSegmentReportResponse, compute
    )


@router.get("/datatalk", response_model=DataTalkResponse)
async def datatalk(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    cache: Cache = Depends(get_cache),
    settings: Settings = Depends(get_settings),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> DataTalkResponse:
    start, end = _default_window(start, end)
    key = _cache_key(tenant_id, "datatalk", start, end)

    async def compute() -> DataTalkResponse:
        report = await GetDataTalk(repo).execute(tenant_id, start, end)
        return _datatalk_response(start, end, report)

    return await _cached(
        cache, key, settings.cache_ttl_seconds, DataTalkResponse, compute
    )


@router.post("/datatalk/snapshot", response_model=DataTalkSnapshotResponse)
async def create_datatalk_snapshot(
    tenant_id: str = Depends(require_tenant),
    repo: SqlAnalyticsRepository = Depends(get_analytics_repo),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> DataTalkSnapshotResponse:
    start, end = _default_window(start, end)
    snapshot = await CreateDataTalkSnapshot(repo).execute(tenant_id, start, end)
    return DataTalkSnapshotResponse(
        schema_version=ANALYTICS_RESPONSE_SCHEMA_VERSION,
        snapshot_id=snapshot.snapshot_id,
        status=snapshot.status,
        generated_at=snapshot.generated_at,
        report=_datatalk_response(start, end, snapshot.report),
    )
