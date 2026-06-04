---
title: Collector production guardrails belong at the ingestion edge
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI ingestion
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Hardening `/v1/collect` beyond a demo contract
  - Splitting IAM ownership into Kotlin while keeping Python as the collector edge
  - Adding Kafka or SQL ingestion paths that acknowledge accepted events
tags: [ingestion, collect-api, kafka, rate-limit, idempotency, operations]
---

# Collector production guardrails belong at the ingestion edge

## Context

The architecture assigns Tenant/IAM lifecycle to Kotlin/Spring, but the Python service still owns the `/v1/collect` edge. That means the Python collector must enforce the operational checks that are only visible at request handling and publish time: payload size, per-tenant request pressure, schema validation, Kafka publish behavior, and SQL idempotency races.

## Guidance

Keep identity lifecycle and policy ownership in Kotlin, but enforce collector-local guardrails where events enter the system.

- Put API key creation, expiry, scope, rotation, and audit policy in Kotlin Tenant/IAM.
- In Python, resolve the tenant context from the provided key or IAM integration and ignore any payload tenant value.
- Limit collect request size before accepting a batch.
- Apply a tenant-scoped rate limit in the collector or API gateway. In-process limits are only a secondary guard for multi-instance deployments.
- Use Kafka producer settings that match operational expectations: `acks=all`, compression, linger, timeout, retry backoff, and bounded publish attempts.
- Treat raw Kafka publish failure as failed ingestion. Do not return accepted counts if the event never reached the stream.
- In SQL fallback mode, use database conflict handling instead of select-then-insert as the only duplicate defense. Concurrent duplicate requests must not fail the API.

## Why This Matters

`/v1/collect` is usually called by browser scripts or server-to-server integrations at high volume. If the edge accepts huge payloads, has no tenant pressure control, or acknowledges Kafka failures as success, downstream analytics silently lose trust. If SQL lite mode relies only on a pre-insert duplicate lookup, concurrent duplicate events may turn into integrity errors instead of duplicate counts.

## When to Apply

- Hardening event collection for pilot or paid tenants.
- Adding a Kafka ingestion mode behind an HTTP collector.
- Keeping a SQL fallback path for local, demo, or low-volume tenants.
- Defining the contract between Kotlin IAM and Python data services.

## Examples

This implementation added:

- `SUNRISE_MAX_COLLECT_PAYLOAD_BYTES` and `SUNRISE_COLLECT_RATE_LIMIT_PER_MINUTE` settings.
- A resettable in-process tenant limiter in `app/core/rate_limit.py`.
- Request byte-limit and rate-limit checks in `app/ingestion/adapters/http.py`.
- Kafka publish attempts and producer tuning through `app/core/config.py` and `app/ingestion/adapters/kafka.py`.
- PostgreSQL/SQLite `ON CONFLICT DO NOTHING ... RETURNING event_id` in `app/ingestion/adapters/repository.py`.
- Tests for 413 payload rejection, 429 tenant throttling, and transient Kafka retry.

Verification:

```bash
pytest
python3 -m compileall app tests
```

## Related

- `docs/solutions/best-practices/kafka-failure-dlq-observability-2026-06-03.md`
- `docs/solutions/best-practices/optional-stream-adapters-lazy-import-2026-06-03.md`
- `docs/solutions/best-practices/clickhouse-compose-mirror-ingestion-2026-06-03.md`
