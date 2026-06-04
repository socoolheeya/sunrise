"""Recommendation model artifact and training tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.recommendation.adapters.model_registry import (
    FEATURE_NAMES,
    load_recommendation_model,
)
from app.recommendation.training.train_ranker import (
    build_artifact,
    load_examples,
    train,
)


def test_default_recommendation_model_artifact_is_valid():
    artifact = load_recommendation_model()

    assert artifact.model_version == "ml.logistic-recommendation.v3"
    assert artifact.feature_version == "events-product-value-features.v1"
    assert artifact.model_type == "logistic_regression"
    assert set(artifact.weights) == set(FEATURE_NAMES)
    assert artifact.metrics["auc"] >= 0.5


def test_training_pipeline_generates_serving_artifact(tmp_path: Path):
    examples = load_examples(Path("tests/fixtures/recommendation_training.csv"))
    bias, weights = train(examples, epochs=50, learning_rate=0.2)
    artifact = build_artifact(
        examples,
        bias,
        weights,
        source="tests/fixtures/recommendation_training.csv",
        model_version="ml.logistic-recommendation.test",
    )

    path = tmp_path / "ranker.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    loaded = load_recommendation_model(str(path))

    assert loaded.model_version == "ml.logistic-recommendation.test"
    assert set(loaded.weights) == set(FEATURE_NAMES)
    assert loaded.metrics["auc"] >= 0.5
