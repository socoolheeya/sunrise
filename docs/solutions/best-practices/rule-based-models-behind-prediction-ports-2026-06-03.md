---
title: Put rule-based prediction models behind stable ports
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI prediction
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Adding prediction APIs before production ML models exist
  - Preserving API contracts while model serving evolves
  - Building lite mode from event-derived features
tags: [prediction, ml-ports, fastapi, rule-based-models]
---

# Put rule-based prediction models behind stable ports

## Context

The Python service added purchase score, churn risk, and product affinity APIs before a production model serving stack exists. The implementation uses event-derived SQL features and rule-based scoring, but every response already includes `model_version`, `feature_version`, and `generated_at`.

## Guidance

Treat rule-based models as replaceable adapters, not as API contracts.

- Keep feature loading behind a repository port.
- Keep scoring in application use cases that can later call a model-serving adapter.
- Include model and feature versions from the first API release.
- Preserve tenant scoping in the feature repository, not in endpoint code alone.
- Test ranking/metadata behavior at the API boundary and scorer behavior at the use-case boundary.

## Why This Matters

Early prediction APIs often start with heuristics. If those heuristics leak into the API shape, replacing them with trained models becomes a breaking change. Stable metadata and ports let the platform mature from lite scoring to feature store/model serving without changing callers.

## When to Apply

- Building prediction endpoints from behavioral events.
- Adding placeholder or MVP ML functionality.
- Preparing APIs for later online scoring or batch score lookup.

## Examples

```python
metadata = {
    "model_version": "rules.purchase-churn-affinity.v1",
    "feature_version": "events-lite.v1",
    "generated_at": generated_at,
}
```

## Related

- `app/prediction/application/scoring.py`
- `app/prediction/domain/repository.py`
- `app/prediction/adapters/repository.py`
- `tests/test_prediction_api.py`
