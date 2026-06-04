"""Train a multi-head logistic prediction artifact.

Input CSV schema:

view_count,cart_add_count,purchase_count,revenue,days_since_seen,days_since_purchase,product_view_count,product_cart_add_count,product_purchase_count,purchase_label,churn_label,affinity_label

Labels are binary. The generated artifact is consumed by
SUNRISE_PREDICTION_MODEL_PATH.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp, log, log1p
from pathlib import Path

from app.prediction.adapters.model_registry import (
    AFFINITY_FEATURE_NAMES,
    VISITOR_FEATURE_NAMES,
)


@dataclass(frozen=True)
class Example:
    visitor_features: dict[str, float]
    affinity_features: dict[str, float]
    purchase_label: int
    churn_label: int
    affinity_label: int


def _cap_log(value: float, cap: int) -> float:
    return max(0.0, min(1.0, log1p(max(value, 0.0)) / log1p(cap)))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def _visitor_features(row: dict[str, str]) -> dict[str, float]:
    days_since_seen = min(float(row.get("days_since_seen") or 365), 90.0)
    days_since_purchase = min(float(row.get("days_since_purchase") or 365), 90.0)
    recency_days = min(days_since_seen, days_since_purchase)
    return {
        "view_signal": _cap_log(float(row.get("view_count") or 0), 50),
        "cart_signal": _cap_log(float(row.get("cart_add_count") or 0), 20),
        "purchase_signal": _cap_log(float(row.get("purchase_count") or 0), 20),
        "revenue_signal": _cap_log(float(row.get("revenue") or 0), 1000),
        "recency_signal": max(0.0, min(1.0, 1.0 - recency_days / 90.0)),
        "inactivity_signal": max(0.0, min(1.0, days_since_seen / 90.0)),
    }


def _affinity_features(row: dict[str, str]) -> dict[str, float]:
    return {
        "view_signal": _cap_log(float(row.get("product_view_count") or 0), 20),
        "cart_signal": _cap_log(float(row.get("product_cart_add_count") or 0), 10),
        "purchase_signal": _cap_log(float(row.get("product_purchase_count") or 0), 10),
    }


def load_examples(path: Path) -> list[Example]:
    examples: list[Example] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            examples.append(
                Example(
                    visitor_features=_visitor_features(row),
                    affinity_features=_affinity_features(row),
                    purchase_label=int(row["purchase_label"]),
                    churn_label=int(row["churn_label"]),
                    affinity_label=int(row["affinity_label"]),
                )
            )
    if not examples:
        raise ValueError("training data is empty")
    return examples


def train_head(
    rows: list[tuple[dict[str, float], int]],
    feature_names: tuple[str, ...],
    *,
    epochs: int = 400,
    learning_rate: float = 0.15,
    l2: float = 0.001,
) -> tuple[float, dict[str, float]]:
    bias = 0.0
    weights = {name: 0.0 for name in feature_names}
    n = len(rows)

    for _ in range(epochs):
        grad_b = 0.0
        grad_w = {name: 0.0 for name in feature_names}
        for features, label in rows:
            logit = bias + sum(weights[name] * features[name] for name in feature_names)
            pred = _sigmoid(logit)
            error = pred - label
            grad_b += error
            for name in feature_names:
                grad_w[name] += error * features[name]

        bias -= learning_rate * (grad_b / n)
        for name in feature_names:
            weights[name] -= learning_rate * (grad_w[name] / n + l2 * weights[name])

    return round(bias, 6), {name: round(value, 6) for name, value in weights.items()}


def predict(
    rows: list[tuple[dict[str, float], int]],
    feature_names: tuple[str, ...],
    bias: float,
    weights: dict[str, float],
) -> list[float]:
    return [
        _sigmoid(bias + sum(weights[name] * features[name] for name in feature_names))
        for features, _ in rows
    ]


def log_loss(labels: list[int], predictions: list[float]) -> float:
    eps = 1e-12
    total = 0.0
    for label, pred in zip(labels, predictions, strict=True):
        p = min(1.0 - eps, max(eps, pred))
        total += -(label * log(p) + (1 - label) * log(1 - p))
    return total / len(labels)


def auc(labels: list[int], predictions: list[float]) -> float:
    pairs = sorted(zip(predictions, labels, strict=True), key=lambda pair: pair[0])
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    rank_sum = 0.0
    for rank, (_, label) in enumerate(pairs, start=1):
        if label == 1:
            rank_sum += rank
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _head_metrics(
    rows: list[tuple[dict[str, float], int]],
    feature_names: tuple[str, ...],
    bias: float,
    weights: dict[str, float],
) -> tuple[float, float]:
    predictions = predict(rows, feature_names, bias, weights)
    labels = [label for _, label in rows]
    return round(auc(labels, predictions), 6), round(log_loss(labels, predictions), 6)


def build_artifact(
    examples: list[Example],
    *,
    source: str,
    model_version: str,
    epochs: int = 400,
    learning_rate: float = 0.15,
    l2: float = 0.001,
) -> dict:
    purchase_rows = [(ex.visitor_features, ex.purchase_label) for ex in examples]
    churn_rows = [(ex.visitor_features, ex.churn_label) for ex in examples]
    affinity_rows = [(ex.affinity_features, ex.affinity_label) for ex in examples]

    purchase_bias, purchase_weights = train_head(
        purchase_rows,
        VISITOR_FEATURE_NAMES,
        epochs=epochs,
        learning_rate=learning_rate,
        l2=l2,
    )
    churn_bias, churn_weights = train_head(
        churn_rows,
        VISITOR_FEATURE_NAMES,
        epochs=epochs,
        learning_rate=learning_rate,
        l2=l2,
    )
    affinity_bias, affinity_weights = train_head(
        affinity_rows,
        AFFINITY_FEATURE_NAMES,
        epochs=epochs,
        learning_rate=learning_rate,
        l2=l2,
    )

    purchase_auc, purchase_loss = _head_metrics(
        purchase_rows, VISITOR_FEATURE_NAMES, purchase_bias, purchase_weights
    )
    churn_auc, churn_loss = _head_metrics(
        churn_rows, VISITOR_FEATURE_NAMES, churn_bias, churn_weights
    )
    affinity_auc, affinity_loss = _head_metrics(
        affinity_rows, AFFINITY_FEATURE_NAMES, affinity_bias, affinity_weights
    )

    return {
        "model_version": model_version,
        "feature_version": "events-ml-features.v1",
        "model_type": "multi_head_logistic_regression",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "visitor_features": list(VISITOR_FEATURE_NAMES),
        "affinity_features": list(AFFINITY_FEATURE_NAMES),
        "biases": {
            "purchase_score": purchase_bias,
            "churn_risk": churn_bias,
            "product_affinity": affinity_bias,
        },
        "heads": {
            "purchase_score": purchase_weights,
            "churn_risk": churn_weights,
            "product_affinity": affinity_weights,
        },
        "metrics": {
            "purchase_auc": purchase_auc,
            "purchase_log_loss": purchase_loss,
            "churn_auc": churn_auc,
            "churn_log_loss": churn_loss,
            "affinity_auc": affinity_auc,
            "affinity_log_loss": affinity_loss,
        },
        "training_data": {
            "source": source,
            "sample_count": len(examples),
            "purchase_positive_rate": round(
                sum(ex.purchase_label for ex in examples) / len(examples), 6
            ),
            "churn_positive_rate": round(
                sum(ex.churn_label for ex in examples) / len(examples), 6
            ),
            "affinity_positive_rate": round(
                sum(ex.affinity_label for ex in examples) / len(examples), 6
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-version", default="ml.logistic-prediction.custom")
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.15)
    parser.add_argument("--l2", type=float, default=0.001)
    args = parser.parse_args()

    examples = load_examples(args.input)
    artifact = build_artifact(
        examples,
        source=str(args.input),
        model_version=args.model_version,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        l2=args.l2,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
