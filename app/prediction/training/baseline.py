"""결정론적 합성 baseline 학습 데이터.

실 이벤트 라벨이 feature store 에서 export 되기 전까지, 패키지에 동봉되는
prediction artifact 는 이 **커밋된 결정론적 합성 데이터셋**으로 학습한다.
목적은 서빙 artifact 가 hand-filled 메타데이터가 아니라 재현 가능한 실제 학습
산출물이 되도록 하는 것이다. (난수 대신 인덱스 기반 LCG 로 완전 결정론적.)

라벨은 feature 와 학습 가능한 상관(+노이즈)을 갖도록 생성한다:
- purchase_label : 조회/장바구니/구매/최근성이 높을수록 1
- churn_label    : 마지막 구매 후 경과일이 크고 구매가 적을수록 1
- affinity_label : 상품 단위 조회/장바구니/구매가 높을수록 1
"""

from __future__ import annotations

import csv
from pathlib import Path

FIELDS = [
    "view_count", "cart_add_count", "purchase_count", "revenue",
    "days_since_seen", "days_since_purchase",
    "product_view_count", "product_cart_add_count", "product_purchase_count",
    "purchase_label", "churn_label", "affinity_label",
]

DEFAULT_ROWS = 240


def baseline_csv_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "baseline_offline.csv"


def _lcg(seed: int):
    state = seed & 0x7FFFFFFF or 1

    def nxt() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return nxt


def generate_rows(n: int = DEFAULT_ROWS) -> list[list[float]]:
    rows: list[list[float]] = []
    for i in range(n):
        r = _lcg((i + 1) * 2654435761)
        view = int(r() * 40)
        cart = int(r() * 8)
        purchase = int(r() * 4)
        revenue = round(purchase * (20 + r() * 180), 2)
        days_since_seen = int(r() * 120)
        days_since_purchase = min(365, days_since_seen + int(r() * 90))
        product_view = int(r() * 15)
        product_cart = int(r() * 5)
        product_purchase = int(r() * 3)

        purchase_score = (
            0.03 * view + 0.25 * cart + 0.6 * purchase
            + (0.6 if days_since_seen < 14 else 0.0)
            + 0.5 * r()
        )
        churn_score = (
            0.018 * days_since_purchase - 0.35 * purchase + 0.5 * r()
        )
        affinity_score = (
            0.12 * product_view + 0.35 * product_cart + 0.8 * product_purchase
            + 0.5 * r()
        )
        # 임계값은 양성률 ~0.35 가 되도록 점수 분포 기준으로 보정.
        purchase_label = 1 if purchase_score > 3.05 else 0
        churn_label = 1 if churn_score > 1.79 else 0
        affinity_label = 1 if affinity_score > 2.90 else 0

        rows.append([
            view, cart, purchase, revenue,
            days_since_seen, days_since_purchase,
            product_view, product_cart, product_purchase,
            purchase_label, churn_label, affinity_label,
        ])
    return rows


def write_baseline_csv(path: Path | None = None, n: int = DEFAULT_ROWS) -> Path:
    target = path or baseline_csv_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_rows(n)
    with target.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        writer.writerows(rows)
    return target


if __name__ == "__main__":
    written = write_baseline_csv()
    print(f"wrote {written}")
