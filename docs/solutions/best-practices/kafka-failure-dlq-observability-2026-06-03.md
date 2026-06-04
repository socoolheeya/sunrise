---
title: Kafka ingestion failures need both DLQ records and visible counters
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI ingestion
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Publishing accepted API payloads to Kafka
  - Adding DLQ behavior for stream ingestion
  - Returning API errors for downstream infrastructure failures
tags: [kafka, dlq, observability, ingestion, fastapi]
---

# Kafka ingestion failures need both DLQ records and visible counters

## Context

The ingestion service added Kafka raw event publishing after the SQL lite path. The next reliability slice added failure handling: raw topic publish failures can write a failure payload to a DLQ topic, surface a 503 to the API caller, and increment counters visible through `/ops/metrics`.

## Guidance

When adding Kafka ingestion failure handling, keep three signals aligned.

- The API should not report accepted events if raw Kafka publish failed.
- The adapter should write a DLQ payload when configured, including the original event and error.
- Metrics should separately count raw publish failures, successful DLQ writes, and DLQ write failures.
- DLQ failure must not hide the original raw publish failure.
- Tests should use fake producers to cover all paths without requiring a Kafka broker.

## Why This Matters

Without DLQ records, operators cannot inspect what failed. Without counters, failures can be invisible until users notice missing analytics data. Without a 503, callers may assume ingestion succeeded even though the event never reached the stream.

## When to Apply

- Adding Kafka producer adapters.
- Building at-least-once ingestion paths.
- Introducing external infrastructure failures into an API path.

## Examples

```python
try:
    await producer.send_and_wait(raw_topic, ...)
except Exception as exc:
    metrics.record_publish_failure()
    await publish_dlq(event, exc)
    raise KafkaPublishError(...)
```

## Related

- `app/ingestion/adapters/kafka.py`
- `app/ingestion/adapters/http.py`
- `app/core/observability.py`
- `tests/test_kafka_ingestion.py`
