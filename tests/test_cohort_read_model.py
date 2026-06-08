"""cohort 일/주/월 retention read model 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

from httpx import AsyncClient

from app.analytics.domain.cohort import build_cohort_rows


def _dt(day: int) -> datetime:
    return datetime(2026, 6, day, tzinfo=timezone.utc)


# ---- 도메인 단위 테스트 ----
def test_build_cohort_rows_daily():
    records = [
        ("v1", _dt(1)), ("v1", _dt(2)),  # offset 0, 1
        ("v2", _dt(1)), ("v2", _dt(3)),  # offset 0, 2
    ]

    rows = build_cohort_rows(records, granularity="day")

    assert len(rows) == 1
    row = rows[0]
    assert row.cohort == "2026-06-01"
    assert row.size == 2
    by_offset = {c.offset: c for c in row.cells}
    assert by_offset[0].active == 2 and by_offset[0].rate == 1.0
    assert by_offset[1].active == 1 and by_offset[1].rate == 0.5
    assert by_offset[2].active == 1 and by_offset[2].rate == 0.5


def test_build_cohort_rows_weekly():
    records = [
        ("v1", _dt(1)), ("v1", _dt(8)),  # 같은 코호트 주, 1주 뒤 재방문
        ("v2", _dt(1)),
    ]

    rows = build_cohort_rows(records, granularity="week")

    row = rows[0]
    assert row.cohort == "2026-06-01"  # 해당 주 월요일
    assert row.size == 2
    by_offset = {c.offset: c for c in row.cells}
    assert by_offset[0].active == 2
    assert by_offset[1].active == 1  # v1 만 다음 주 재방문


def test_build_cohort_rows_respects_max_offset():
    records = [("v1", _dt(1)), ("v1", _dt(10))]  # offset 0, 9

    rows = build_cohort_rows(records, granularity="day", max_offset=3)

    offsets = [c.offset for c in rows[0].cells]
    assert max(offsets) == 3  # 9 까지 있지만 max_offset=3 으로 절단


# ---- 머티리얼라이즈 + 조회 통합 테스트 ----
async def test_cohort_refresh_and_retention(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={
            "events": [
                {"event_id": "co-1", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o1", "amount": 100, "occurred_at": "2026-06-01T00:00:00Z"},
                {"event_id": "co-2", "visitor_id": "v1", "type": "purchase",
                 "order_id": "o2", "amount": 100, "occurred_at": "2026-06-02T00:00:00Z"},
                {"event_id": "co-3", "visitor_id": "v2", "type": "purchase",
                 "order_id": "o3", "amount": 50, "occurred_at": "2026-06-01T00:00:00Z"},
            ]
        },
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-10T00:00:00Z",
              "cohort_type": "purchase", "granularity": "day"}

    refresh = await client.post("/v1/analytics/cohort/refresh", params=params)
    retention = await client.get(
        "/v1/analytics/cohort/retention",
        params={"cohort_type": "purchase", "granularity": "day"},
    )

    assert refresh.status_code == 200
    assert refresh.json()["materialized_cells"] == 2  # offset 0, 1
    assert retention.status_code == 200
    rows = retention.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["cohort"] == "2026-06-01"
    assert rows[0]["size"] == 2
    by_offset = {c["offset"]: c for c in rows[0]["cells"]}
    assert by_offset[0]["rate"] == 1.0
    assert by_offset[1]["active"] == 1 and by_offset[1]["rate"] == 0.5


async def test_cohort_refresh_rematerialize_replaces_cells(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "rc-1", "visitor_id": "v1", "type": "purchase",
             "order_id": "o1", "amount": 100, "occurred_at": "2026-06-01T00:00:00Z"},
        ]},
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-10T00:00:00Z",
              "cohort_type": "purchase", "granularity": "day"}

    first = await client.post("/v1/analytics/cohort/refresh", params=params)
    second = await client.post("/v1/analytics/cohort/refresh", params=params)
    retention = await client.get(
        "/v1/analytics/cohort/retention",
        params={"cohort_type": "purchase", "granularity": "day"},
    )

    assert first.json()["materialized_cells"] == 1
    assert second.json()["materialized_cells"] == 1  # delete-then-insert, 중복 없음
    assert len(retention.json()["rows"]) == 1
    assert len(retention.json()["rows"][0]["cells"]) == 1


async def test_cohort_visit_type_uses_view_events(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "cv-1", "visitor_id": "v1", "type": "view",
             "occurred_at": "2026-06-01T00:00:00Z"},
            {"event_id": "cv-2", "visitor_id": "v1", "type": "view",
             "occurred_at": "2026-06-02T00:00:00Z"},
        ]},
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-10T00:00:00Z",
              "cohort_type": "visit", "granularity": "day"}

    await client.post("/v1/analytics/cohort/refresh", params=params)
    retention = await client.get(
        "/v1/analytics/cohort/retention",
        params={"cohort_type": "visit", "granularity": "day"},
    )

    rows = retention.json()["rows"]
    assert rows[0]["size"] == 1
    assert {c["offset"] for c in rows[0]["cells"]} == {0, 1}


async def test_cohort_rejects_invalid_params(client: AsyncClient):
    bad_type = await client.post(
        "/v1/analytics/cohort/refresh",
        params={"cohort_type": "bogus", "granularity": "day"},
    )
    bad_gran = await client.get(
        "/v1/analytics/cohort/retention",
        params={"cohort_type": "purchase", "granularity": "hour"},
    )
    assert bad_type.status_code == 422
    assert bad_gran.status_code == 422


async def test_cohort_enforces_tenant_isolation(client: AsyncClient):
    await client.post(
        "/v1/collect",
        json={"events": [
            {"event_id": "ct-1", "visitor_id": "v1", "type": "purchase",
             "order_id": "o1", "amount": 100, "occurred_at": "2026-06-01T00:00:00Z"},
        ]},
    )
    params = {"start": "2026-06-01T00:00:00Z", "end": "2026-06-10T00:00:00Z",
              "cohort_type": "purchase", "granularity": "day"}
    await client.post("/v1/analytics/cohort/refresh", params=params)

    other = await client.get(
        "/v1/analytics/cohort/retention",
        params={"cohort_type": "purchase", "granularity": "day"},
        headers={"X-Sunrise-Key": "other-key"},
    )
    assert other.status_code == 200
    assert other.json()["rows"] == []  # tenant-b 는 코호트 없음
