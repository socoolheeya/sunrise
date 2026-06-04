"""Prediction model artifact loader.

This is the model registry boundary for prediction. Production can point
SUNRISE_PREDICTION_MODEL_PATH at a promoted artifact. Local/dev uses the
packaged artifact so API contracts and scoring behavior remain deterministic.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from app.prediction.domain.model import PredictionModelArtifact

VISITOR_FEATURE_NAMES = (
    "view_signal",
    "cart_signal",
    "purchase_signal",
    "revenue_signal",
    "recency_signal",
    "inactivity_signal",
)
AFFINITY_FEATURE_NAMES = (
    "view_signal",
    "cart_signal",
    "purchase_signal",
)
HEADS = ("purchase_score", "churn_risk", "product_affinity")


def _default_artifact_text() -> str:
    return (
        resources.files("app.prediction.models")
        .joinpath("prediction_model.json")
        .read_text(encoding="utf-8")
    )


def _read_artifact(path: str | None) -> dict[str, Any]:
    if path is None:
        return json.loads(_default_artifact_text())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate(raw: dict[str, Any]) -> PredictionModelArtifact:
    model_type = raw.get("model_type")
    if model_type != "multi_head_logistic_regression":
        raise ValueError(f"unsupported prediction model_type: {model_type}")

    visitor_features = tuple(raw.get("visitor_features", []))
    if visitor_features != VISITOR_FEATURE_NAMES:
        raise ValueError("prediction visitor features do not match serving contract")

    affinity_features = tuple(raw.get("affinity_features", []))
    if affinity_features != AFFINITY_FEATURE_NAMES:
        raise ValueError("prediction affinity features do not match serving contract")

    heads = raw.get("heads")
    biases = raw.get("biases")
    if not isinstance(heads, dict) or not isinstance(biases, dict):
        raise ValueError("prediction model heads and biases must be objects")

    parsed_heads: dict[str, dict[str, float]] = {}
    parsed_biases: dict[str, float] = {}
    for head in HEADS:
        if head not in heads or head not in biases:
            raise ValueError(f"prediction model is missing head: {head}")
        feature_names = (
            AFFINITY_FEATURE_NAMES if head == "product_affinity" else VISITOR_FEATURE_NAMES
        )
        weights = heads[head]
        if not isinstance(weights, dict):
            raise ValueError(f"prediction model head {head} weights must be an object")
        missing = [name for name in feature_names if name not in weights]
        if missing:
            raise ValueError(f"prediction model head {head} is missing weights: {missing}")
        parsed_heads[head] = {name: float(weights[name]) for name in feature_names}
        parsed_biases[head] = float(biases[head])

    metrics = raw.get("metrics") or {}
    for metric in ("purchase_auc", "churn_auc", "affinity_auc"):
        if float(metrics.get(metric, 0.0)) < 0.5:
            raise ValueError(f"prediction model {metric} must be at least 0.5")

    return PredictionModelArtifact(
        model_version=str(raw["model_version"]),
        feature_version=str(raw["feature_version"]),
        model_type=model_type,
        visitor_features=VISITOR_FEATURE_NAMES,
        affinity_features=AFFINITY_FEATURE_NAMES,
        heads=parsed_heads,
        biases=parsed_biases,
        metrics={key: float(value) for key, value in metrics.items()},
        training_data=dict(raw.get("training_data") or {}),
    )


@lru_cache(maxsize=8)
def load_prediction_model(path: str | None = None) -> PredictionModelArtifact:
    """Load and validate a promoted prediction model artifact."""
    return _validate(_read_artifact(path))
