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
    weights = dict.fromkeys(feature_names, 0.0)
    n = len(rows)

    for _ in range(epochs):
        grad_b = 0.0
        grad_w = dict.fromkeys(feature_names, 0.0)
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


def _holdout_split(
    examples: list[Example], holdout_every: int = 5
) -> tuple[list[Example], list[Example]]:
    """결정론적 holdout 분할(매 holdout_every 번째 행). 재현 가능."""
    holdout = [ex for i, ex in enumerate(examples) if i % holdout_every == 0]
    train = [ex for i, ex in enumerate(examples) if i % holdout_every != 0]
    if not train or not holdout:
        return examples, []
    return train, holdout


def _backtest(
    examples: list[Example],
    *,
    epochs: int,
    learning_rate: float,
    l2: float,
) -> dict:
    """train 분할로 재학습 후 holdout 으로 out-of-sample 지표를 산출한다."""
    train, holdout = _holdout_split(examples)
    if not holdout:
        return {"holdout_size": 0, "note": "dataset too small for holdout"}

    def _eval(label_attr: str, feature_attr: str, feature_names: tuple[str, ...]):
        train_rows = [(getattr(ex, feature_attr), getattr(ex, label_attr)) for ex in train]
        holdout_rows = [(getattr(ex, feature_attr), getattr(ex, label_attr)) for ex in holdout]
        bias, weights = train_head(
            train_rows, feature_names, epochs=epochs, learning_rate=learning_rate, l2=l2
        )
        return _head_metrics(holdout_rows, feature_names, bias, weights)

    purchase_auc, purchase_loss = _eval("purchase_label", "visitor_features", VISITOR_FEATURE_NAMES)
    churn_auc, churn_loss = _eval("churn_label", "visitor_features", VISITOR_FEATURE_NAMES)
    affinity_auc, affinity_loss = _eval("affinity_label", "affinity_features", AFFINITY_FEATURE_NAMES)
    return {
        "holdout_size": len(holdout),
        "train_size": len(train),
        "split": "deterministic_every_5",
        "purchase_auc": purchase_auc,
        "purchase_log_loss": purchase_loss,
        "churn_auc": churn_auc,
        "churn_log_loss": churn_loss,
        "affinity_auc": affinity_auc,
        "affinity_log_loss": affinity_loss,
    }


def _drift_baseline(examples: list[Example]) -> dict[str, float]:
    """학습 데이터의 visitor feature 평균 분포 (서빙 drift 비교 기준)."""
    n = len(examples)
    return {
        name: round(sum(ex.visitor_features[name] for ex in examples) / n, 6)
        for name in VISITOR_FEATURE_NAMES
    }


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
        # holdout 기반 out-of-sample 지표(과적합 방지 검증용). metrics 는 in-sample.
        "backtest": _backtest(
            examples, epochs=epochs, learning_rate=learning_rate, l2=l2
        ),
        # 학습 feature 분포(서빙 drift 비교 기준).
        "drift_baseline": _drift_baseline(examples),
    }


def artifact_without_trained_at(artifact: dict) -> dict:
    """재현성 비교용: 매 실행마다 달라지는 trained_at 만 제거."""
    return {key: value for key, value in artifact.items() if key != "trained_at"}


def main() -> None:
    from app.prediction.training.baseline import baseline_csv_path, write_baseline_csv

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path, default=None,
        help="학습 CSV. 미지정 시 결정론적 baseline 데이터셋을 생성/사용.",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("app/prediction/models/prediction_model.json"),
    )
    parser.add_argument("--model-version", default="ml.logistic-prediction.v3")
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.15)
    parser.add_argument("--l2", type=float, default=0.001)
    parser.add_argument(
        "--check", action="store_true",
        help="기존 --output 이 현재 학습 데이터로 재현되는지만 검증(쓰기 없음).",
    )
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        input_path = baseline_csv_path()
        if not input_path.exists():
            write_baseline_csv(input_path)
        # 머신 독립적·재현 가능한 안정 식별자(절대경로 임베드 방지).
        source = "baseline_offline.csv (deterministic synthetic)"
    else:
        source = str(input_path)

    examples = load_examples(input_path)
    artifact = build_artifact(
        examples,
        source=source,
        model_version=args.model_version,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        l2=args.l2,
    )

    if args.check:
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if artifact_without_trained_at(existing) != artifact_without_trained_at(artifact):
            raise SystemExit(
                "served artifact is NOT reproducible from training data "
                "(hand-edit or drifted source detected)"
            )
        print("OK: served artifact is reproducible from training data")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
