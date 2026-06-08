"""FastAPI 컴포지션 루트.

수집(Ingestion)과 분석/지표(Analytics) 라우터를 조립한다.
lite 버전은 기동 시 테이블을 생성한다(운영은 Alembic 마이그레이션).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import status
from fastapi.responses import JSONResponse

from app.ai.adapters.http import router as ai_router
from app.analytics.adapters.clickhouse import ClickHouseQueryError
from app.analytics.adapters.http import close_analytics_backend
from app.analytics.adapters.http import configure_analytics_backend
from app.analytics.adapters.http import router as analytics_router
from app.audience.adapters.http import router as audience_router
from app.core.cache import close_cache
from app.core.config import get_settings
from app.core.database import close_database
from app.core.database import init_models
from app.core.observability import get_ingestion_metrics
from app.ingestion.adapters.http import router as ingestion_router
from app.ingestion.adapters.http import close_ingestion_sink, configure_ingestion_sink
from app.onsite.adapters.http import router as onsite_router
from app.prediction.adapters.http import router as prediction_router
from app.recommendation.adapters.http import router as recommendation_router
from app.registry.adapters.http import router as registry_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 운영(auto_create_tables=False)에서는 Alembic 마이그레이션으로 스키마를 관리한다.
    settings = get_settings()
    if settings.auto_create_tables:
        await init_models()
    await configure_analytics_backend(app, settings)
    await configure_ingestion_sink(app, settings)
    try:
        yield
    finally:
        await close_analytics_backend(app)
        await close_ingestion_sink(app)
        await close_cache()
        await close_database()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    app.include_router(ingestion_router)
    app.include_router(analytics_router)
    app.include_router(audience_router)
    app.include_router(prediction_router)
    app.include_router(recommendation_router)
    app.include_router(onsite_router)
    app.include_router(ai_router)
    app.include_router(registry_router)

    @app.exception_handler(ClickHouseQueryError)
    async def clickhouse_query_error_handler(_, __) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "ClickHouse backend is unavailable"},
        )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/ops/metrics", tags=["ops"])
    async def ops_metrics() -> dict[str, dict[str, int]]:
        metrics = get_ingestion_metrics()
        return {
            "ingestion": {
                "accepted_events": metrics.accepted_events,
                "duplicate_events": metrics.duplicate_events,
                "publish_failures": metrics.publish_failures,
                "dlq_published": metrics.dlq_published,
                "dlq_failures": metrics.dlq_failures,
            }
        }

    return app


app = create_app()
