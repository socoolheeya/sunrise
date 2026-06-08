"""Recommendation model artifact loader.

This adapter represents the model registry boundary. Production can point
SUNRISE_RECOMMENDATION_MODEL_PATH at a promoted artifact. Local/dev uses the
artifact packaged with the service.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from app.recommendation.domain.model import RecommendationModelArtifact

FEATURE_NAMES = (
    "view_signal",
    "cart_signal",
    "purchase_signal",
    "buyer_signal",
    "category_affinity",
    "previously_viewed",
    "placement_message",
    "placement_onsite",
    "relative_value_signal",
    "discount_signal",
    "rating_signal",
    "review_confidence",
    "return_quality_signal",
    "margin_signal",
)


def _default_artifact_text() -> str:
    return (
        resources.files("app.recommendation.models")
        .joinpath("recommendation_ranker.json")
        .read_text(encoding="utf-8")
    )


def _read_artifact(path: str | None) -> dict[str, Any]:
    if path is None:
        return json.loads(_default_artifact_text())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate(raw: dict[str, Any]) -> RecommendationModelArtifact:
    model_type = raw.get("model_type")
    if model_type != "logistic_regression":
        raise ValueError(f"unsupported recommendation model_type: {model_type}")

    features = tuple(raw.get("features", []))
    if features != FEATURE_NAMES:
        raise ValueError(
            "recommendation model features do not match serving feature contract"
        )

    weights = raw.get("weights")
    if not isinstance(weights, dict):
        raise ValueError("recommendation model weights must be an object")
    missing = [name for name in FEATURE_NAMES if name not in weights]
    if missing:
        raise ValueError(f"recommendation model is missing weights: {missing}")

    metrics = raw.get("metrics") or {}
    auc = float(metrics.get("auc", 0.0))
    if auc < 0.5:
        raise ValueError("recommendation model auc must be at least 0.5")

    return RecommendationModelArtifact(
        model_version=str(raw["model_version"]),
        feature_version=str(raw["feature_version"]),
        model_type=model_type,
        bias=float(raw["bias"]),
        weights={name: float(weights[name]) for name in FEATURE_NAMES},
        metrics={key: float(value) for key, value in metrics.items()},
        training_data=dict(raw.get("training_data") or {}),
    )


@lru_cache(maxsize=8)
def load_recommendation_model(path: str | None = None) -> RecommendationModelArtifact:
    """Load and validate a promoted model artifact."""
    return _validate(_read_artifact(path))


def validate_recommendation_artifact(raw: dict[str, Any]) -> RecommendationModelArtifact:
    """DB 레지스트리 raw artifact 검증/파싱(서빙 계약 강제)."""
    return _validate(raw)
