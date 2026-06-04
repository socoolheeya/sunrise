---
title: ClickHouse analytics should stay behind the repository port
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI analytics
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Moving analytics reads from SQL lite mode to OLAP storage
  - Adding ClickHouse without rewriting application use cases
  - Testing OLAP query behavior without external infrastructure
tags: [clickhouse, analytics, repository-port, clean-architecture]
---

# ClickHouse analytics should stay behind the repository port

## Context

The analytics service added `SUNRISE_ANALYTICS_BACKEND=clickhouse` while preserving the existing SQL lite path. Existing use cases such as dashboard metrics, funnel, cohort, and benchmark kept depending on `AnalyticsRepository`.

## Guidance

Keep ClickHouse as an outbound adapter behind the existing repository port.

- Do not let application use cases import ClickHouse clients or SQL strings.
- Select SQL vs ClickHouse in the FastAPI dependency/composition layer.
- Lazy import `clickhouse-connect` so SQL mode and tests remain independent from OLAP dependencies.
- Test ClickHouse behavior with a fake client that records queries and returns named rows.
- Keep tenant filters inside every tenant-scoped query and reserve platform-wide queries for explicit benchmark methods.

## Why This Matters

Analytics storage will evolve from SQLite/PostgreSQL to ClickHouse materialized views. If use cases depend directly on ClickHouse details, every backend change becomes a product-layer refactor. Keeping the port stable lets the service switch storage without changing API behavior.

## When to Apply

- Adding ClickHouse, Druid, BigQuery, or any OLAP backend.
- Migrating from raw SQL event scans to materialized read models.
- Preserving local lite mode while adding production infrastructure.

## Examples

```python
if settings.analytics_backend == "clickhouse":
    return ClickHouseAnalyticsRepository(client, settings.clickhouse_events_table)
return SqlAnalyticsRepository(session)
```

## Related

- `app/analytics/domain/repository.py`
- `app/analytics/adapters/clickhouse.py`
- `app/analytics/adapters/http.py`
- `tests/test_clickhouse_analytics.py`
