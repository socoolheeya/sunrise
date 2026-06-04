---
title: Optional stream adapters should lazy import external clients
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI ingestion
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Adding an operational adapter that requires external infrastructure
  - Keeping local lite mode and tests independent from Kafka or Redis
  - Introducing dependencies that are only needed for one runtime mode
tags: [kafka, lazy-import, fastapi, optional-dependencies]
---

# Optional stream adapters should lazy import external clients

## Context

The ingestion service added a Kafka sink while preserving the existing SQL lite mode. Local tests and default development should continue to run without Kafka brokers or Kafka client startup.

## Guidance

Keep optional infrastructure clients behind runtime configuration and lazy imports.

- Default to the local adapter (`SUNRISE_INGESTION_SINK=sql`) for development and tests.
- Initialize Kafka only in the FastAPI lifespan when `SUNRISE_INGESTION_SINK=kafka`.
- Import `aiokafka` inside the Kafka producer factory, not at module import time.
- Test the Kafka repository with a fake producer so adapter behavior is covered without a broker.
- Return a clear readiness error if Kafka mode is configured but no producer is available.

## Why This Matters

Eager imports or unconditional startup of infrastructure clients make local tests brittle and slow. They also turn optional operational capabilities into global application requirements. Lazy imports keep the code compiled, tested, and usable in SQL mode while still making Kafka mode explicit.

## When to Apply

- Adding Kafka, Redis, ClickHouse, vector database, or provider SDK adapters.
- Supporting both lite/local mode and production mode in the same service.
- Introducing a dependency that is not needed for domain or application tests.

## Examples

```python
async def create_aiokafka_producer(bootstrap_servers: str) -> KafkaProducer:
    try:
        from aiokafka import AIOKafkaProducer
    except ImportError as exc:
        raise RuntimeError("Kafka ingestion mode requires aiokafka") from exc
```

```python
if settings.ingestion_sink == "kafka":
    app.state.kafka_producer = await create_aiokafka_producer(...)
```

## Related

- `app/ingestion/adapters/kafka.py`
- `app/ingestion/adapters/http.py`
- `app/core/config.py`
- `tests/test_kafka_ingestion.py`
