---
title: Compose-enabled adapters need packaged client dependencies
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI infrastructure
problem_type: best_practice
component: tooling
severity: high
applies_when:
  - Docker Compose enables Redis, ClickHouse, Kafka, or another optional adapter by environment variable.
  - Unit tests use fake clients or lazy imports while the deployed app uses real infrastructure clients.
  - Reviewing whether a PRD feature is actually usable end to end, not only implemented behind a port.
tags: [docker-compose, dependencies, runtime-verification, fastapi, adapters]
---

# Compose-enabled adapters need packaged client dependencies

## Context

The Python PRD review found that tests and compile checks passed while the compose runtime still could fail. `docker-compose.yml` enables `SUNRISE_REDIS_URL`, `SUNRISE_ANALYTICS_BACKEND=clickhouse`, and `SUNRISE_CLICKHOUSE_MIRROR_INGESTION=true`, but `requirements.txt` initially did not include `redis` or `clickhouse-connect`.

This is easy to miss because local SQL-mode tests use fake clients and lazy imports, so the optional adapters stay unexercised unless the runtime configuration activates them. A second pass also caught that `clickhouse-connect==0.9.2` would install on Python 3.12 but not the local Python 3.14 test environment; the dependency had to move to `clickhouse-connect==1.1.1`.

## Guidance

When compose or production-like configuration enables an optional adapter, check both sides of the contract:

- The service code has a working adapter path for the configured backend.
- The packaged environment installs the concrete client library used by that adapter.
- The startup path is verified under the same environment variables used by compose.

For this repo, a PRD implementation review should include at least:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m compileall app tests
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -c "import importlib.util; print(bool(importlib.util.find_spec('redis'))); print(bool(importlib.util.find_spec('clickhouse_connect')))"
docker compose config
docker compose build app
```

If compose enables ClickHouse or Redis, absence of those imports means the feature is not fully usable even if adapter unit tests pass. If dependency installation fails on a newer local Python, choose a version compatible with both the local verifier and the Docker runtime, then build the image to confirm the production interpreter still resolves the package.

## Why This Matters

Lazy imports are the right shape for optional infrastructure, but they also move failures from import time to configured runtime paths. A test suite with fake clients proves the contract shape; it does not prove that the deployable image can start with real Redis or ClickHouse enabled.

## When to Apply

- Adding a new infrastructure adapter behind a repository or cache port.
- Changing `docker-compose.yml` environment variables to enable a non-default backend.
- Answering whether PRD functionality is ready for end-to-end use.

## Examples

During the PRD status review:

- `/v1/collect`, analytics, prediction, and recommendation routes were registered and tested.
- `/v1/ai/diagnoses/site`, `/v1/ai/suggestions/campaigns`, and `/v1/ai/copy` were not registered.
- `venv/bin/python -m pytest -q` passed with 56 tests.
- `venv/bin/python -m compileall app tests` passed.
- `redis` and `clickhouse_connect` were not importable, despite compose enabling those runtime paths.

During the completion pass:

- AI Agent/Copy routes were added and OpenAPI listed all PRD endpoints.
- Tests increased to 62 and passed.
- `venv/bin/python -m pip install -r requirements.txt` caught the bad `clickhouse-connect==0.9.2` pin for Python 3.14.
- `clickhouse-connect==1.1.1` and `redis==5.2.1` installed locally and in the Python 3.12 Docker image.
- `docker compose up -d db redis clickhouse app` produced healthy services, and live `/v1/collect` plus `/v1/ai/copy` smoke requests succeeded.

## Related

- `docs/solutions/best-practices/optional-stream-adapters-lazy-import-2026-06-03.md`
- `docs/solutions/best-practices/clickhouse-compose-mirror-ingestion-2026-06-03.md`
- `requirements.txt`
- `docker-compose.yml`
- `app/core/cache.py`
- `app/analytics/adapters/clickhouse.py`
