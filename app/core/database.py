"""데이터베이스 연결/세션 관리 (SQLAlchemy 2.0 async)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.orm import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def init_models() -> None:
    """테이블 생성. lite/로컬/테스트용. 운영에서는 Alembic 마이그레이션 사용."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_event_attribution_columns)


def _ensure_event_attribution_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    if "events" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("events")}
    columns = {
        "session_id": "VARCHAR(128)",
        "order_id": "VARCHAR(128)",
        "utm_source": "VARCHAR(128)",
        "utm_medium": "VARCHAR(128)",
        "utm_campaign": "VARCHAR(128)",
        "landing_page": "VARCHAR(2048)",
    }
    quote = connection.dialect.identifier_preparer.quote
    for name, sql_type in columns.items():
        if name in existing:
            continue
        connection.execute(
            text(f"ALTER TABLE events ADD COLUMN {quote(name)} {sql_type}")
        )


async def get_session() -> AsyncIterator[AsyncSession]:
    """요청 스코프 세션 의존성."""
    async with get_sessionmaker()() as session:
        yield session


async def close_database() -> None:
    """애플리케이션 종료 시 DB pool 을 정리한다."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_state() -> None:
    """테스트 격리용: 캐시된 엔진/세션메이커 초기화."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
