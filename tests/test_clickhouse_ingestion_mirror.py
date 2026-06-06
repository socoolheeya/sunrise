"""SQL 수집 후 ClickHouse mirror 경로 테스트."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient


class FakeClickHouseInsertClient:
    def __init__(self) -> None:
        self.inserts: list[dict] = []

    def insert(self, table, data, column_names):
        self.inserts.append(
            {"table": table, "data": data, "column_names": column_names}
        )


def _event(event_id: str, visitor_id: str = "v1"):
    return {
        "event_id": event_id,
        "visitor_id": visitor_id,
        "type": "view",
        "occurred_at": "2026-06-01T00:00:00Z",
        "product_id": "p1",
    }


async def _client_with_mirror(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("SUNRISE_API_KEYS", '{"test-key": "tenant-a"}')
    monkeypatch.setenv("SUNRISE_CLICKHOUSE_MIRROR_INGESTION", "true")
    monkeypatch.setenv("SUNRISE_CLICKHOUSE_EVENTS_TABLE", "sunrise.events")

    from app.core import cache, config, database, observability
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    await init_models()

    app = create_app()
    fake = FakeClickHouseInsertClient()
    app.state.clickhouse_client = fake
    transport = ASGITransport(app=app)
    client = AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    )
    return client, fake


async def test_sql_collect_mirrors_only_accepted_events_to_clickhouse(monkeypatch, tmp_path):
    client, fake = await _client_with_mirror(monkeypatch, tmp_path)
    async with client:
        first = await client.post(
            "/v1/collect",
            json={"events": [_event("e1"), _event("e1"), _event("e2", "v2")]},
        )
        second = await client.post(
            "/v1/collect",
            json={"events": [_event("e1"), _event("e2", "v2")]},
        )

    assert first.status_code == 202
    assert first.json()["accepted"] == 2
    assert first.json()["duplicates"] == 1
    assert second.status_code == 202
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 2
    assert len(fake.inserts) == 1
    assert fake.inserts[0]["table"] == "sunrise.events"
    assert len(fake.inserts[0]["data"]) == 2
    assert fake.inserts[0]["column_names"] == [
        "tenant_id",
        "event_id",
        "visitor_id",
        "type",
        "product_id",
        "category",
        "session_id",
        "order_id",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "landing_page",
        "amount",
        "occurred_at",
        "received_at",
    ]


async def test_clickhouse_mirror_requires_ready_client(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("SUNRISE_API_KEYS", '{"test-key": "tenant-a"}')
    monkeypatch.setenv("SUNRISE_CLICKHOUSE_MIRROR_INGESTION", "true")

    from app.core import cache, config, database, observability
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    await init_models()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.post("/v1/collect", json={"events": [_event("e1")]})

    assert response.status_code == 503
    assert response.json()["detail"] == "ClickHouse client is not ready"
