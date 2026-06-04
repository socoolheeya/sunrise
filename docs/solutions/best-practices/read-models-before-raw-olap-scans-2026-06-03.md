---
title: Prefer read model tables before raw OLAP scans
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI analytics
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Optimizing analytics endpoints over ClickHouse
  - Adding materialized views or daily aggregate tables
  - Keeping raw event scans as a fallback path
tags: [clickhouse, read-models, materialized-views, analytics]
---

# Prefer read model tables before raw OLAP scans

## Context

The ClickHouse analytics adapter initially queried raw event/read event tables for every metric request. The next Phase 3 slice added `SUNRISE_CLICKHOUSE_METRIC_DAILY_TABLE`, allowing dashboard and benchmark metrics to read from a daily aggregate table while funnel and cohort queries continue to use event-level data.

## Guidance

Put materialized read models behind the same repository port as raw event queries.

- Keep raw event scans as the fallback when no read model table is configured.
- Use explicit settings for each read model table instead of overloading the raw events table name.
- Document the expected read model columns in README or schema docs.
- Test that enabling the read model avoids raw aggregate functions such as `uniqExact`.
- Keep endpoint and use case behavior unchanged while swapping the storage path.

## Why This Matters

Raw OLAP scans are useful during early development, but high-traffic dashboard APIs should query pre-aggregated tables. A read model setting lets production use materialized views without forcing local lite mode or application use cases to change.

## When to Apply

- Adding daily, hourly, or tenant-scoped aggregate tables.
- Moving metrics from raw events to ClickHouse materialized views.
- Introducing performance optimization without changing API contracts.

## Examples

```python
if self._metric_daily_table is not None:
    return await self._metric_inputs_from_daily(...)
return await self._metric_inputs(...)
```

## Related

- `app/analytics/adapters/clickhouse.py`
- `app/core/config.py`
- `tests/test_clickhouse_analytics.py`
- `README.md`
