---
title: ClickHouse recommendation product features must use the same serving path
date: 2026-06-03
category: docs/solutions/runtime-errors
module: Python FastAPI recommendation
problem_type: runtime_error
component: service_object
severity: high
symptoms:
  - "`POST /v1/recommendations/products` returned 500 in Docker Compose production mode."
  - "`POST /v1/recommendations/items` returned empty items when out-of-stock exclusion was enabled."
root_cause: incomplete_setup
resolution_type: code_fix
related_components:
  - database
  - testing_framework
tags: [clickhouse, recommendation, product-features, feature-store, docker-compose]
---

# ClickHouse recommendation product features must use the same serving path

## Problem

The product value feature endpoint worked in SQL tests but failed in Docker Compose production mode. Compose selects `SUNRISE_ANALYTICS_BACKEND=clickhouse`, so recommendations are served by `ClickHouseRecommendationRepository`, not `SqlRecommendationRepository`.

## Symptoms

- `/v1/recommendations/products` returned `Internal Server Error`.
- App logs showed `AttributeError: 'ClickHouseRecommendationRepository' object has no attribute 'upsert_product_features'`.
- After adding ClickHouse upsert, `/v1/recommendations/items` returned 200 but an empty item list when `exclude_out_of_stock=true`.

## What Didn't Work

Adding `product_features` only to SQLAlchemy and SQLite tests did not prove the production path. The HTTP endpoint depended on `get_recommendation_repo()`, which returns ClickHouse under compose, so a SQL-only upsert method left the live adapter without the port method.

Using `ifNull(p.in_stock, true)` in the ClickHouse join was also insufficient. With default LEFT JOIN settings, missing right-side non-nullable Bool values may materialize as `false`, not `NULL`, so products with no feature row were treated as out of stock.

## Solution

Keep product feature reads and writes behind the same recommendation repository port:

- Add `upsert_product_features()` to `RecommendationRepository`.
- Implement it in both `SqlRecommendationRepository` and `ClickHouseRecommendationRepository`.
- Add `sunrise.product_features_v1` as a ClickHouse `ReplacingMergeTree(updated_at)` read model.
- Wire `SUNRISE_CLICKHOUSE_PRODUCT_FEATURES_TABLE` in `docker-compose.yml`.
- Join latest product features into ClickHouse recommendation candidates with `argMax(..., updated_at)`.
- Default missing ClickHouse product feature rows to in stock by checking join presence: `if(p.product_id = '', true, p.in_stock) AS in_stock`.

## Why This Works

The recommendation serving path has two modes: SQL lite mode and ClickHouse production mode. Both modes need the same repository contract, because the HTTP layer should not know which backend is active. The ClickHouse table stores append-only replacements, while `argMax` gives the latest product feature value at serving time.

The explicit join-presence check avoids ClickHouse default-value behavior for non-nullable Bool columns. Missing product value data should mean "unknown value features", not "out of stock".

## Prevention

When adding a feature-store write path:

- Extend the repository port first, then implement every configured adapter.
- Add fake-client tests for ClickHouse query text and insert calls.
- Include the read model table in both init SQL and compose environment variables.
- Run the full local checks and a Docker Compose smoke script after fresh build.

Verification commands used:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m compileall app tests
venv/bin/python -m json.tool docs/api/sunrise.postman_collection.json
bash -n docs/api/curl_smoke.sh
docker compose build app
docker compose up -d app
docs/api/curl_smoke.sh
```

## Related

- `app/recommendation/domain/repository.py`
- `app/recommendation/adapters/clickhouse.py`
- `app/recommendation/adapters/repository.py`
- `app/recommendation/adapters/http.py`
- `clickhouse/init/001_events.sql`
- `docker-compose.yml`
- `tests/test_clickhouse_feature_read_models.py`
- `tests/test_streaming_clickhouse_contract.py`
- `docs/solutions/architecture-patterns/ml-recommendation-ranking-contract-2026-06-03.md`
- `docs/solutions/best-practices/clickhouse-adapters-behind-repository-port-2026-06-03.md`
