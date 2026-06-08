"""다중 granularity(일/주/월) 코호트 retention 빌더.

기존 model.CohortReport.from_purchases 는 월간 전용이다. 이 모듈은 일/주/월
버킷을 일반화해 cohort_retention read model 머티리얼라이즈에 사용한다.
외부 프레임워크에 의존하지 않는 순수 도메인 로직.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from app.analytics.domain.model import CohortCell, CohortRow

GRANULARITIES = ("day", "week", "month")
DEFAULT_MAX_OFFSET = 11  # architecture §6.6: 0~11 일/주/월

# 코호트 정의 이벤트 타입.
_VISIT_EVENT_TYPES = ("view", "category_view", "cart_add", "cart_remove", "purchase")
_PURCHASE_EVENT_TYPES = ("purchase",)


def cohort_event_types(cohort_type: str) -> tuple[str, ...]:
    if cohort_type == "purchase":
        return _PURCHASE_EVENT_TYPES
    if cohort_type == "visit":
        return _VISIT_EVENT_TYPES
    raise ValueError(f"unsupported cohort_type: {cohort_type}")


def _bucket(value: datetime, granularity: str) -> date:
    day = value.date()
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())  # 해당 주 월요일
    return day.replace(day=1)  # month


def _offset(cohort_bucket: date, bucket: date, granularity: str) -> int:
    if granularity == "day":
        return (bucket - cohort_bucket).days
    if granularity == "week":
        return (bucket - cohort_bucket).days // 7
    return (bucket.year - cohort_bucket.year) * 12 + (bucket.month - cohort_bucket.month)


def _label(bucket: date, granularity: str) -> str:
    if granularity == "month":
        return f"{bucket.year:04d}-{bucket.month:02d}"
    return bucket.isoformat()  # day/week 는 ISO 날짜(주는 월요일)로 식별


def build_cohort_rows(
    records: Iterable[tuple[str, datetime]],
    *,
    granularity: str,
    max_offset: int = DEFAULT_MAX_OFFSET,
) -> list[CohortRow]:
    """(visitor_id, occurred_at) 기록으로 코호트 retention 매트릭스를 구성한다.

    - 코호트 = 방문자의 첫 활동 버킷.
    - offset = 코호트 버킷 대비 경과 버킷 수(0 = 첫 버킷).
    - 0..max_offset 범위만 산출.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"unsupported granularity: {granularity}")

    by_visitor: dict[str, set[date]] = {}
    for visitor_id, occurred_at in records:
        by_visitor.setdefault(visitor_id, set()).add(_bucket(occurred_at, granularity))

    cohorts: dict[date, list[set[int]]] = {}
    for buckets in by_visitor.values():
        first = min(buckets)
        offsets = {_offset(first, b, granularity) for b in buckets}
        cohorts.setdefault(first, []).append(offsets)

    rows: list[CohortRow] = []
    for cohort in sorted(cohorts):
        visitors = cohorts[cohort]
        size = len(visitors)
        present_max = max((max(offsets) for offsets in visitors), default=0)
        upper = min(present_max, max_offset)
        cells = tuple(
            CohortCell(
                offset=k,
                active=(active := sum(1 for offsets in visitors if k in offsets)),
                rate=active / size if size else 0.0,
            )
            for k in range(upper + 1)
        )
        rows.append(CohortRow(cohort=_label(cohort, granularity), size=size, cells=cells))
    return rows
