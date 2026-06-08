"""확률 CLV 모델(BG/NBD + Gamma-Gamma) 단위 + BG/NBD 서빙 경로 테스트."""

from __future__ import annotations

from math import log

from httpx import AsyncClient

from app.prediction.domain.clv_models import (
    BgNbdParams,
    ClvCustomer,
    GammaGammaParams,
    bgnbd_expected_purchases,
    bgnbd_p_alive,
    fit_bgnbd,
    fit_gamma_gamma,
    gamma_gamma_expected_value,
    hyp2f1,
)

PARAMS = BgNbdParams(r=0.5, alpha=10.0, a=1.2, b=2.5)


# ---- 도메인 단위 ----
def test_hyp2f1_matches_closed_form():
    # 2F1(1,1;2;z) = -ln(1-z)/z
    z = 0.5
    assert abs(hyp2f1(1.0, 1.0, 2.0, z) - (-log(1 - z) / z)) < 1e-9


def test_p_alive_recent_higher_than_stale():
    recent = bgnbd_p_alive(PARAMS, frequency=5, recency=95, T=100)
    stale = bgnbd_p_alive(PARAMS, frequency=5, recency=20, T=100)
    assert recent > stale
    # 반복구매 없는 고객은 항상 alive=1
    assert bgnbd_p_alive(PARAMS, frequency=0, recency=0, T=100) == 1.0


def test_expected_purchases_monotonic_and_nonnegative():
    active = bgnbd_expected_purchases(PARAMS, frequency=8, recency=95, T=100, horizon=90)
    churned = bgnbd_expected_purchases(PARAMS, frequency=8, recency=10, T=100, horizon=90)
    assert active > churned >= 0.0


def test_gamma_gamma_shrinks_toward_population_mean():
    params = GammaGammaParams(population_mean=100.0, shrinkage=5.0)
    # 구매 많을수록 고객 관측 평균에 가까워진다
    few = gamma_gamma_expected_value(params, purchases=1, monetary=300.0)
    many = gamma_gamma_expected_value(params, purchases=20, monetary=300.0)
    assert 100.0 < few < many < 300.0


def test_fit_returns_finite_positive_params():
    customers = [
        ClvCustomer(
            frequency=float(i % 5),
            recency=float((i % 5) * 15),
            T=120.0,
            monetary=50.0 + (i % 7) * 10,
            purchases=(i % 5) + 1,
        )
        for i in range(40)
    ]
    bgnbd = fit_bgnbd(customers)
    gamma = fit_gamma_gamma(customers)
    assert bgnbd is not None
    assert all(v > 0 for v in (bgnbd.r, bgnbd.alpha, bgnbd.a, bgnbd.b))
    assert gamma is not None and gamma.population_mean > 0

    # 표본 부족 → None (호출측 휴리스틱 fallback)
    assert fit_bgnbd(customers[:3]) is None


# ---- 서빙 경로(>=5 고객 → BG/NBD 적용) ----
async def test_clv_api_uses_bgnbd_when_population_sufficient(client: AsyncClient):
    events = []
    # 6명의 구매 고객: 첫 구매(2026-02-01) + 반복 구매(최근성 상이)
    repeats = {
        "c1": "2026-06-20", "c2": "2026-06-10", "c3": "2026-05-20",
        "c4": "2026-04-10", "c5": "2026-03-10", "c6": "2026-02-10",
    }
    for cid, repeat_day in repeats.items():
        events.append({"event_id": f"{cid}-p1", "visitor_id": cid, "type": "purchase",
                       "order_id": f"{cid}-o1", "amount": 100,
                       "occurred_at": "2026-02-01T00:00:00Z"})
        events.append({"event_id": f"{cid}-p2", "visitor_id": cid, "type": "purchase",
                       "order_id": f"{cid}-o2", "amount": 120,
                       "occurred_at": f"{repeat_day}T00:00:00Z"})
    await client.post("/v1/collect", json={"events": events})

    response = await client.post(
        "/v1/predictions/clv",
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-07-01T00:00:00Z",
                "horizon_days": 180},
        json={"visitor_ids": ["c1", "c6"]},
    )

    assert response.status_code == 200
    by_visitor = {v["visitor_id"]: v for v in response.json()["values"]}
    # 확률 모델 경로가 사용됨(휴리스틱 아님)
    assert "bgnbd_gamma_gamma" in by_visitor["c1"]["reasons"]
    # 최근 재구매(c1)가 오래된 재구매(c6)보다 생존확률·기대구매가 높다
    # (BG/NBD 조건부식은 모수와 무관하게 recency 에 단조 증가)
    assert by_visitor["c1"]["survival_probability"] >= by_visitor["c6"]["survival_probability"]
    assert by_visitor["c1"]["expected_purchases"] >= by_visitor["c6"]["expected_purchases"]
    # Gamma-Gamma 기대 주문금액은 양수(관측 110 ↔ 모집단 평균 사이)
    assert by_visitor["c1"]["expected_order_value"] > 0
    assert by_visitor["c1"]["predicted_clv"] >= 0
