"""테스트 공통 픽스처.

외부 인프라 없이 동작하도록 임시 파일 SQLite 를 사용한다.
설정/DB 싱글턴은 테스트마다 초기화해 격리한다.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "SUNRISE_API_KEYS",
        '{"test-key": "tenant-a", "other-key": "tenant-b"}',
    )

    from app.core import cache, config, database, model_registry, observability, rate_limit

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    rate_limit.reset_state()
    model_registry.reset_parsed_cache()
    yield
    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    rate_limit.reset_state()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    from app.core.database import init_models
    from app.main import create_app

    await init_models()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as c:
        yield c
