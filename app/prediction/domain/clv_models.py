"""확률 기반 CLV 모델 (순수 stdlib).

- BG/NBD: 거래 빈도(frequency), 최근성(recency), 관측기간(T)으로 향후 기대 거래수와
  생존확률 P(alive) 산출. 모수(r, alpha, a, b)는 Nelder-Mead MLE 로 적합.
- Gamma-Gamma(posterior mean): 고객 평균 주문금액을 모집단 평균으로 수축(shrinkage)해
  기대 주문금액 산출. 모수는 method-of-moments 로 추정.

외부 라이브러리(lifetimes/scipy) 없이 동작한다. 데이터가 부족하거나 적합이 실패하면
호출측이 보수적 fallback 으로 되돌아갈 수 있도록 None 을 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, isfinite, lgamma, log


@dataclass(frozen=True)
class ClvCustomer:
    frequency: float  # 반복 거래 수 (x = 총구매 - 1)
    recency: float    # 첫 구매~마지막 구매 경과(단위: 일)
    T: float          # 첫 구매~관측종료 경과(단위: 일)
    monetary: float   # 평균 주문금액 (m̄x)
    purchases: int    # 총 구매 수 (monetary 산출 분모)


@dataclass(frozen=True)
class BgNbdParams:
    r: float
    alpha: float
    a: float
    b: float


@dataclass(frozen=True)
class GammaGammaParams:
    population_mean: float
    shrinkage: float  # k = (q-1)/p, 클수록 모집단 평균으로 강하게 수축


def _logaddexp(x: float, y: float) -> float:
    if x == -inf:
        return y
    if y == -inf:
        return x
    hi = max(x, y)
    return hi + log(exp(x - hi) + exp(y - hi))


def hyp2f1(a: float, b: float, c: float, z: float, *, max_iter: int = 500, tol: float = 1e-12) -> float:
    """Gaussian hypergeometric 2F1 급수합 (|z|<1 수렴)."""
    term = 1.0
    total = 1.0
    for n in range(max_iter):
        term *= (a + n) * (b + n) / ((c + n) * (1.0 + n)) * z
        total += term
        if abs(term) < tol:
            break
    return total


def bgnbd_loglike(params: BgNbdParams, customers: list[ClvCustomer]) -> float:
    r, alpha, a, b = params.r, params.alpha, params.a, params.b
    if min(r, alpha, a, b) <= 0:
        return -inf
    total = 0.0
    for c in customers:
        x, t_x, T = c.frequency, c.recency, c.T
        ln_a1 = lgamma(r + x) - lgamma(r) + r * log(alpha)
        ln_a2 = lgamma(a + b) - lgamma(b) + lgamma(b + x) - lgamma(a + b + x)
        ln_a3 = -(r + x) * log(alpha + T)
        if x > 0:
            ln_a4 = log(a) - log(b + x - 1) - (r + x) * log(alpha + t_x)
            term = _logaddexp(ln_a3, ln_a4)
        else:
            term = ln_a3
        total += ln_a1 + ln_a2 + term
    return total


def _nelder_mead(fn, x0, *, max_iter=400, step=0.5):
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        pt = list(x0)
        pt[i] += step
        simplex.append(pt)
    scores = [fn(p) for p in simplex]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: scores[i])
        simplex = [simplex[i] for i in order]
        scores = [scores[i] for i in order]
        if abs(scores[-1] - scores[0]) < 1e-9:
            break
        centroid = [sum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        worst = simplex[-1]
        reflected = [centroid[j] + (centroid[j] - worst[j]) for j in range(n)]
        rs = fn(reflected)
        if rs < scores[0]:
            expanded = [centroid[j] + 2.0 * (centroid[j] - worst[j]) for j in range(n)]
            es = fn(expanded)
            simplex[-1], scores[-1] = (expanded, es) if es < rs else (reflected, rs)
        elif rs < scores[-2]:
            simplex[-1], scores[-1] = reflected, rs
        else:
            contracted = [centroid[j] + 0.5 * (worst[j] - centroid[j]) for j in range(n)]
            cs = fn(contracted)
            if cs < scores[-1]:
                simplex[-1], scores[-1] = contracted, cs
            else:
                best = simplex[0]
                for i in range(1, n + 1):
                    simplex[i] = [best[j] + 0.5 * (simplex[i][j] - best[j]) for j in range(n)]
                    scores[i] = fn(simplex[i])
    best_i = min(range(n + 1), key=lambda i: scores[i])
    return simplex[best_i], scores[best_i]


def fit_bgnbd(customers: list[ClvCustomer], *, min_customers: int = 5) -> BgNbdParams | None:
    usable = [c for c in customers if c.T > 0]
    if len(usable) < min_customers:
        return None

    def neg_ll(theta: list[float]) -> float:
        try:
            r, alpha, a, b = (exp(t) for t in theta)
        except OverflowError:
            return inf
        if not all(isfinite(v) for v in (r, alpha, a, b)):
            return inf
        ll = bgnbd_loglike(BgNbdParams(r, alpha, a, b), usable)
        return -ll if isfinite(ll) else inf

    theta0 = [log(1.0), log(14.0), log(1.2), log(2.5)]
    best, score = _nelder_mead(neg_ll, theta0)
    if not isfinite(score):
        return None
    r, alpha, a, b = (exp(t) for t in best)
    if not all(isfinite(v) and v > 0 for v in (r, alpha, a, b)):
        return None
    return BgNbdParams(r, alpha, a, b)


def bgnbd_p_alive(params: BgNbdParams, frequency: float, recency: float, T: float) -> float:
    r, alpha, a, b = params.r, params.alpha, params.a, params.b
    if frequency <= 0:
        return 1.0
    ratio = (a / (b + frequency - 1)) * ((alpha + T) / (alpha + recency)) ** (r + frequency)
    return 1.0 / (1.0 + ratio)


def bgnbd_expected_purchases(
    params: BgNbdParams, frequency: float, recency: float, T: float, horizon: float
) -> float:
    r, alpha, a, b = params.r, params.alpha, params.a, params.b
    a_eff = max(a, 1.0 + 1e-6)  # 조건부 기대식은 a>1 가정
    z = horizon / (alpha + T + horizon)
    hyp = hyp2f1(r + frequency, b + frequency, a_eff + b + frequency - 1, z)
    numerator = (a_eff + b + frequency - 1) / (a_eff - 1)
    multiplier = 1.0 - ((alpha + T) / (alpha + T + horizon)) ** (r + frequency) * hyp
    if frequency > 0:
        denom = 1.0 + (a / (b + frequency - 1)) * ((alpha + T) / (alpha + recency)) ** (r + frequency)
    else:
        denom = 1.0
    value = numerator * multiplier / denom
    return max(0.0, value)


def fit_gamma_gamma(customers: list[ClvCustomer], *, min_customers: int = 5) -> GammaGammaParams | None:
    means = [c.monetary for c in customers if c.purchases > 0 and c.monetary > 0]
    if len(means) < min_customers:
        return None
    n = len(means)
    population_mean = sum(means) / n
    variance = sum((m - population_mean) ** 2 for m in means) / n
    if variance <= 0:
        shrinkage = 50.0  # 분산 0 → 모집단 평균으로 강하게 수축
    else:
        # k = E[M]^2 / Var[M] (MoM, 분산 클수록 고객 관측을 신뢰 → 작은 k)
        shrinkage = max(0.5, min(50.0, population_mean**2 / variance))
    return GammaGammaParams(population_mean=population_mean, shrinkage=shrinkage)


def gamma_gamma_expected_value(
    params: GammaGammaParams, purchases: int, monetary: float
) -> float:
    """Gamma-Gamma posterior mean: 고객 평균을 모집단 평균으로 수축."""
    x = max(purchases, 0)
    k = params.shrinkage
    return (params.population_mean * k + x * monetary) / (x + k)
