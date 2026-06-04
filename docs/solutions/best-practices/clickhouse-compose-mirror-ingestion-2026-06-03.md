---
title: Compose ClickHouse analytics needs an ingestion mirror for local E2E
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI ClickHouse compose
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Running analytics against ClickHouse in docker compose
  - Keeping SQL lite ingestion while testing OLAP reads
  - Adding ClickHouse healthchecks and credentials to compose
tags: [clickhouse, docker-compose, ingestion-mirror, e2e]
---

# Compose ClickHouse analytics needs an ingestion mirror for local E2E

## Context

ClickHouse analytics was implemented behind the repository port, but compose still needed a practical path from `/v1/collect` to ClickHouse. Without that bridge, PostgreSQL accepted events while ClickHouse-backed analytics returned empty results.

## Guidance

For local compose E2E, mirror only SQL-accepted events into ClickHouse.

- Keep PostgreSQL as the source of ingestion idempotency in lite mode.
- Mirror only newly accepted events, not duplicates.
- Initialize ClickHouse tables with compose-mounted SQL.
- Configure ClickHouse credentials explicitly; otherwise the official image disables network access for the default user.
- Use a healthcheck that works inside the ClickHouse container. In this setup, `0.0.0.0:8123/ping` worked while `localhost:8123` did not.

## Why This Matters

ClickHouse read APIs can look correct in unit tests while failing in compose if no events reach ClickHouse. A local mirror makes end-to-end verification possible without introducing Kafka/Flink into the development stack.

## When to Apply

- Adding local ClickHouse services to docker compose.
- Testing OLAP-backed analytics before the production stream processor exists.
- Reusing SQL ingestion for local demos and smoke tests.

## Examples

```yaml
SUNRISE_ANALYTICS_BACKEND: clickhouse
SUNRISE_CLICKHOUSE_MIRROR_INGESTION: "true"
SUNRISE_CLICKHOUSE_EVENTS_TABLE: sunrise.events
```

## Related

- `docker-compose.yml`
- `clickhouse/init/001_events.sql`
- `app/ingestion/adapters/clickhouse.py`
- `tests/test_clickhouse_ingestion_mirror.py`
