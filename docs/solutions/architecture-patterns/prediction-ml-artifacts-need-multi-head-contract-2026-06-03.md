---
title: Prediction ML artifacts need a multi-head feature contract
date: 2026-06-03
category: docs/solutions/architecture-patterns
module: Python FastAPI prediction
problem_type: architecture_pattern
component: service_object
severity: medium
applies_when:
  - Replacing rule-based purchase, churn, or affinity scoring with ML-backed prediction
  - Serving multiple prediction heads from one promoted artifact
  - Keeping prediction HTTP contracts stable while model internals change
tags: [prediction, ml-artifact, feature-contract, model-registry, logistic-model]
---

# Prediction ML artifacts need a multi-head feature contract

## Context

The prediction API initially exposed stable purchase-score, churn-risk, and product-affinity endpoints, but the implementation used rule formulas. That was useful for lite mode, but not enough for commercial operation where model versions, feature versions, offline metrics, artifact promotion, and retraining matter.

## Guidance

Keep HTTP contracts and repository ports stable, but move scoring behind a validated model artifact.

- Define explicit visitor-level features for purchase/churn heads.
- Define explicit product/category features for affinity scoring.
- Load a promoted artifact through a model registry adapter.
- Validate model type, feature order, required heads, weights, biases, and minimum metrics before serving.
- Return artifact `model_version` and `feature_version` in every API response.
- Keep cold-start policy outside the model where it is a serving rule, not a learned coefficient.
- Include a training script that can regenerate the serving artifact from labeled CSV data.

## Why This Matters

Without a feature contract, an ML replacement can silently change score semantics while the API shape stays the same. Without artifact validation, a bad model file can serve partial or misordered features. Without training code and metrics, a "model" is just hardcoded coefficients with no operational lifecycle.

## When to Apply

- Moving from hand-authored prediction formulas to logistic regression, GBDT, or remote model serving.
- Sharing one event-derived feature source across purchase, churn, and affinity predictions.
- Adding model registry, rollout, or rollback behavior later.

## Examples

This implementation added:

- `app/prediction/models/prediction_model.json` as the packaged promoted artifact.
- `app/prediction/adapters/model_registry.py` to validate model type, feature contract, heads, weights, and metrics.
- `PredictionModelArtifact`, `VisitorPredictionFeatures`, and `ProductAffinityFeatures` domain contracts.
- `MultiHeadLogisticPredictionModel` in `app/prediction/application/scoring.py`.
- `SUNRISE_PREDICTION_MODEL_PATH` for production artifact override.
- `app/prediction/training/train_model.py` and `tests/fixtures/prediction_training.csv`.
- Tests proving the default artifact is valid and the training pipeline can generate a loadable artifact.

Verification:

```bash
pytest
python3 -m compileall app tests
```

## Related

- `docs/solutions/best-practices/rule-based-models-behind-prediction-ports-2026-06-03.md`
- `docs/solutions/architecture-patterns/ml-recommendation-ranking-contract-2026-06-03.md`
