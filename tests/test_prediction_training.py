"""Prediction model artifact and training tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.prediction.adapters.model_registry import (
    AFFINITY_FEATURE_NAMES,
    VISITOR_FEATURE_NAMES,
    load_prediction_model,
)
from app.prediction.training.baseline import baseline_csv_path
from app.prediction.training.train_model import (
    artifact_without_trained_at,
    build_artifact,
    load_examples,
)


def test_default_prediction_model_artifact_is_valid():
    artifact = load_prediction_model()

    assert artifact.model_version == "ml.logistic-prediction.v3"
    assert artifact.feature_version == "events-ml-features.v1"
    assert artifact.model_type == "multi_head_logistic_regression"
    assert artifact.visitor_features == VISITOR_FEATURE_NAMES
    assert artifact.affinity_features == AFFINITY_FEATURE_NAMES
    assert artifact.metrics["purchase_auc"] >= 0.5
    assert artifact.metrics["churn_auc"] >= 0.5
    assert artifact.metrics["affinity_auc"] >= 0.5
    # holdout backtest 와 drift_baseline 이 실제로 산출되어 있어야 한다.
    assert artifact.backtest["holdout_size"] > 0
    assert artifact.backtest["purchase_auc"] >= 0.5
    assert set(VISITOR_FEATURE_NAMES) <= set(artifact.drift_baseline)


def test_served_artifact_is_reproducible_from_training_data():
    """서빙 artifact 가 커밋된 baseline 데이터셋의 학습 산출물과 일치해야 한다."""
    examples = load_examples(baseline_csv_path())
    regenerated = build_artifact(
        examples,
        source="baseline_offline.csv (deterministic synthetic)",
        model_version="ml.logistic-prediction.v3",
    )
    served = json.loads(
        Path("app/prediction/models/prediction_model.json").read_text(encoding="utf-8")
    )
    # trained_at(매 실행 변동) 외 모든 필드(weights/metrics/backtest/drift)가 동일.
    assert artifact_without_trained_at(served) == artifact_without_trained_at(regenerated)


def test_loader_rejects_hand_filled_training_metadata(tmp_path: Path):
    """학습 코드 출력 스키마와 다른 hand-filled training_data 는 거부된다."""
    served = json.loads(
        Path("app/prediction/models/prediction_model.json").read_text(encoding="utf-8")
    )
    served["training_data"] = {  # 과거 hand-filled 스키마
        "source": "offline_event_labels",
        "sample_count": 184230,
        "positive_rate": 0.213,
    }
    bad = tmp_path / "handfilled.json"
    bad.write_text(json.dumps(served), encoding="utf-8")

    with pytest.raises(ValueError, match="hand-filled"):
        load_prediction_model(str(bad))


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
