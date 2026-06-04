"""Train a production recommendation logistic ranker artifact.

Input CSV schema:

visitor_id,product_id,category,view_count,cart_add_count,purchase_count,buyer_count,category_affinity,previously_viewed,placement,price,original_price,category_avg_price,rating,review_count,return_rate,gross_margin,label

`label` is 1 for a positive outcome such as purchase-after-impression and 0 for
negative impressions. The script writes the model artifact consumed by
SUNRISE_RECOMMENDATION_MODEL_PATH.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp, log, log1p
from pathlib import Path

from app.recommendation.adapters.model_registry import FEATURE_NAMES


@dataclass(frozen=True)
class Example:
    features: dict[str, float]
    label: int


def _cap_log(value: float, cap: int) -> float:
    return max(0.0, min(1.0, log1p(max(value, 0.0)) / log1p(cap)))


def _float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return float(value)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ratio_signal(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or denominator <= 0:
        return 0.0
    return _clamp01((numerator / denominator) / 2.0)


def _discount_signal(price: float | None, original_price: float | None) -> float:
    if price is None or original_price is None or original_price <= 0:
        return 0.0
    return _clamp01((original_price - price) / original_price)


def _margin_signal(gross_margin: float | None, price: float | None) -> float:
    if gross_margin is None:
        return 0.0
    if 0 <= gross_margin <= 1:
        return _clamp01(gross_margin)
    if price is None or price <= 0:
        return 0.0
    return _clamp01(gross_margin / price)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def load_examples(path: Path) -> list[Example]:
    examples: list[Example] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            placement = row.get("placement", "widget")
            price = _float(row, "price")
            original_price = _float(row, "original_price")
            category_avg_price = _float(row, "category_avg_price")
            rating = _float(row, "rating")
            return_rate = _float(row, "return_rate")
            gross_margin = _float(row, "gross_margin")
            features = {
                "view_signal": _cap_log(float(row.get("view_count") or 0), 50),
                "cart_signal": _cap_log(float(row.get("cart_add_count") or 0), 20),
                "purchase_signal": _cap_log(float(row.get("purchase_count") or 0), 20),
                "buyer_signal": _cap_log(float(row.get("buyer_count") or 0), 20),
                "category_affinity": float(row.get("category_affinity") or 0),
                "previously_viewed": float(row.get("previously_viewed") or 0),
                "placement_message": 1.0 if placement == "message" else 0.0,
                "placement_onsite": 1.0 if placement == "onsite" else 0.0,
                "relative_value_signal": _ratio_signal(category_avg_price, price),
                "discount_signal": _discount_signal(price, original_price),
                "rating_signal": _clamp01((rating or 0.0) / 5.0),
                "review_confidence": _cap_log(float(row.get("review_count") or 0), 500),
                "return_quality_signal": _clamp01(1.0 - (return_rate or 0.0)),
                "margin_signal": _margin_signal(gross_margin, price),
            }
            examples.append(Example(features=features, label=int(row["label"])))
    if not examples:
        raise ValueError("training data is empty")
    return examples


def train(
    examples: list[Example],
    *,
    epochs: int = 400,
    learning_rate: float = 0.15,
    l2: float = 0.001,
) -> tuple[float, dict[str, float]]:
    bias = 0.0
    weights = {name: 0.0 for name in FEATURE_NAMES}
    n = len(examples)

    for _ in range(epochs):
        grad_b = 0.0
        grad_w = {name: 0.0 for name in FEATURE_NAMES}
        for ex in examples:
            logit = bias + sum(weights[name] * ex.features[name] for name in FEATURE_NAMES)
            pred = _sigmoid(logit)
            error = pred - ex.label
            grad_b += error
            for name in FEATURE_NAMES:
                grad_w[name] += error * ex.features[name]

        bias -= learning_rate * (grad_b / n)
        for name in FEATURE_NAMES:
            regularized = grad_w[name] / n + l2 * weights[name]
            weights[name] -= learning_rate * regularized

    return round(bias, 6), {name: round(value, 6) for name, value in weights.items()}


def predict(examples: list[Example], bias: float, weights: dict[str, float]) -> list[float]:
    return [
        _sigmoid(bias + sum(weights[name] * ex.features[name] for name in FEATURE_NAMES))
        for ex in examples
    ]


def log_loss(examples: list[Example], predictions: list[float]) -> float:
    eps = 1e-12
    total = 0.0
    for ex, pred in zip(examples, predictions, strict=True):
        p = min(1.0 - eps, max(eps, pred))
        total += -(ex.label * log(p) + (1 - ex.label) * log(1 - p))
    return total / len(examples)


def auc(examples: list[Example], predictions: list[float]) -> float:
    pairs = sorted(zip(predictions, examples, strict=True), key=lambda pair: pair[0])
    positives = sum(ex.label for _, ex in pairs)
    negatives = len(pairs) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    rank_sum = 0.0
    for rank, (_, ex) in enumerate(pairs, start=1):
        if ex.label == 1:
            rank_sum += rank
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def precision_at_k(examples: list[Example], predictions: list[float], k: int) -> float:
    top = sorted(zip(predictions, examples, strict=True), key=lambda pair: pair[0], reverse=True)[:k]
    if not top:
        return 0.0
    return sum(ex.label for _, ex in top) / len(top)


def recall_at_k(examples: list[Example], predictions: list[float], k: int) -> float:
    positives = sum(ex.label for ex in examples)
    if positives == 0:
        return 0.0
    top = sorted(zip(predictions, examples, strict=True), key=lambda pair: pair[0], reverse=True)[:k]
    return sum(ex.label for _, ex in top) / positives


def build_artifact(
    examples: list[Example],
    bias: float,
    weights: dict[str, float],
    *,
    source: str,
    model_version: str,
) -> dict:
    predictions = predict(examples, bias, weights)
    k = min(5, len(examples))
    positive_rate = sum(ex.label for ex in examples) / len(examples)
    return {
        "model_version": model_version,
        "feature_version": "events-product-value-features.v1",
        "model_type": "logistic_regression",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": list(FEATURE_NAMES),
        "bias": bias,
        "weights": weights,
        "metrics": {
            "auc": round(auc(examples, predictions), 6),
            "log_loss": round(log_loss(examples, predictions), 6),
            "precision_at_5": round(precision_at_k(examples, predictions, k), 6),
            "recall_at_5": round(recall_at_k(examples, predictions, k), 6),
        },
        "training_data": {
            "source": source,
            "positive_label": "purchase_after_impression",
            "negative_label": "impression_without_purchase",
            "sample_count": len(examples),
            "positive_rate": round(positive_rate, 6),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-version", default="ml.logistic-recommendation.custom")
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.15)
    parser.add_argument("--l2", type=float, default=0.001)
    args = parser.parse_args()

    examples = load_examples(args.input)
    bias, weights = train(
        examples,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        l2=args.l2,
    )
    artifact = build_artifact(
        examples,
        bias,
        weights,
        source=str(args.input),
        model_version=args.model_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
