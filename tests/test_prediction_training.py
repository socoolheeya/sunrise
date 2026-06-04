"""Prediction model artifact and training tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.prediction.adapters.model_registry import (
    AFFINITY_FEATURE_NAMES,
    VISITOR_FEATURE_NAMES,
    load_prediction_model,
)
from app.prediction.training.train_model import build_artifact, load_examples


def test_default_prediction_model_artifact_is_valid():
    artifact = load_prediction_model()

    assert artifact.model_version == "ml.logistic-prediction.v2"
    assert artifact.feature_version == "events-ml-features.v1"
    assert artifact.model_type == "multi_head_logistic_regression"
    assert artifact.visitor_features == VISITOR_FEATURE_NAMES
    assert artifact.affinity_features == AFFINITY_FEATURE_NAMES
    assert artifact.metrics["purchase_auc"] >= 0.5
    assert artifact.metrics["churn_auc"] >= 0.5
    assert artifact.metrics["affinity_auc"] >= 0.5


def test_prediction_training_pipeline_generates_serving_artifact(tmp_path: Path):
    examples = load_examples(Path("tests/fixtures/prediction_training.csv"))
    artifact = build_artifact(
        examples,
        source="tests/fixtures/prediction_training.csv",
        model_version="ml.logistic-prediction.test",
        epochs=80,
        learning_rate=0.2,
    )

    path = tmp_path / "prediction.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    loaded = load_prediction_model(str(path))

    assert loaded.model_version == "ml.logistic-prediction.test"
    assert loaded.visitor_features == VISITOR_FEATURE_NAMES
    assert loaded.affinity_features == AFFINITY_FEATURE_NAMES
    assert loaded.metrics["purchase_auc"] >= 0.5
