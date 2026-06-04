---
title: ML recommendation ranking needs a feature and model contract
date: 2026-06-03
category: docs/solutions/architecture-patterns
module: Python FastAPI recommendation
problem_type: architecture_pattern
component: service_object
severity: medium
applies_when:
  - Replacing a rule-based recommender with an ML ranking model.
  - Keeping the recommendation HTTP API stable while changing scoring internals.
  - Introducing model_version and feature_version as explicit response metadata.
tags: [recommendation, ml-ranking, feature-contract, logistic-model, fastapi]
---

# ML recommendation ranking needs a feature and model contract

## Context

The recommendation service moved from a rule formula based on popularity and category affinity to an ML ranker. The API contract stayed stable, but the ranking internals changed from `rules.candidate-popularity-affinity.v1` to a versioned artifact-backed model, currently `ml.logistic-recommendation.v2`.

The contract later expanded from CRM/event-only features to product value features. The current artifact is `ml.logistic-recommendation.v3` with `events-product-value-features.v1`, which includes price, category average price, discount, rating, review count, return rate, margin, and inventory signals.

## Guidance

When replacing rule recommendation with ML ranking, keep three boundaries explicit:

- Candidate generation: gathers a broad item pool from tenant-scoped product signals.
- Feature extraction: converts candidate + visitor + placement into a normalized feature vector.
- Feature store/read models: provide product value and quality attributes through the same repository port used by serving.
- Model registry: loads a promoted JSON artifact and validates feature order, weights, metrics, and model type.
- Model scoring: computes `sigmoid(bias + sum(weight_i * feature_i))` from the loaded artifact and returns a probability-like score.
- Training pipeline: creates the artifact from labeled impression/outcome data and records metrics such as AUC, log loss, precision@5, and recall@5.

Keep business filters outside the model:

- already purchased exclusion
- already viewed exclusion
- out-of-stock exclusion
- placement limit

Those rules are serving constraints, not model features.

If the production backend is ClickHouse, the product value feature path must exist in ClickHouse too. A SQL-only `product_features` table is not enough because compose resolves the recommendation repository to `ClickHouseRecommendationRepository`.

## Why This Matters

Without a feature contract, an ML replacement becomes a hidden rewrite of the whole recommendation flow. Separating feature extraction from scoring keeps tests focused and lets the model artifact change later without rewriting the HTTP adapter or repository. Without an artifact loader and training script, a logistic scorer is just hardcoded coefficients, not an operational model boundary.

## When to Apply

- Moving from hand-authored ranking formulas to logistic regression, GBDT, neural rankers, or model-serving APIs.
- Adding offline-trained model coefficients while local development still uses event-derived SQL features.
- Preserving existing `/v1/recommendations/items` consumers during scoring changes.

## Examples

The current implementation uses:

- `RecommendationFeatures` as the feature contract.
- `load_recommendation_model()` as the model registry adapter.
- `LogisticRecommendationModel` as the serving model adapter.
- `app/recommendation/training/train_ranker.py` to train and emit artifacts.
- `model_version = "ml.logistic-recommendation.v3"` in the packaged artifact.
- `feature_version = "events-product-value-features.v1"`.
- `POST /v1/recommendations/products` to upsert price/value/quality features.
- `clickhouse/init/001_events.sql` to define `product_features_v1` for production serving.

Verification commands:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m compileall app tests
venv/bin/python -m app.recommendation.training.train_ranker \
  --input tests/fixtures/recommendation_training.csv \
  --output /tmp/recommendation_ranker_test.json \
  --model-version ml.logistic-recommendation.cli-test \
  --epochs 50 \
  --learning-rate 0.2
docker compose build app
docker compose up -d app
curl -sS -X POST 'http://127.0.0.1:8000/v1/recommendations/items?start=2026-06-01T00:00:00Z&end=2026-06-04T00:00:00Z' \
  -H 'Content-Type: application/json' \
  -H 'X-Sunrise-Key: demo-key' \
  -d '{"visitor_id":"v1","placement":"widget","limit":3,"exclude_purchased":true}'
```

## Related

- `app/recommendation/domain/model.py`
- `app/recommendation/application/recommend.py`
- `app/recommendation/adapters/model_registry.py`
- `app/recommendation/training/train_ranker.py`
- `app/recommendation/models/recommendation_ranker.json`
- `tests/test_recommendation_api.py`
- `tests/test_recommendation_training.py`
- `tests/test_clickhouse_feature_read_models.py`
- `docs/prd_python.md`
- `docs/solutions/runtime-errors/clickhouse-recommendation-product-feature-path-2026-06-03.md`
