"""주문 단위 사실 테이블(order_fact) 도메인.

raw 행동 이벤트는 한 주문(order_id)에 대해 여러 purchase 이벤트를 낼 수 있다
(재시도, 분할발송, 스크립트 중복 발화 등). PRD §4.1/§4.2 는 지표/유입/숨은매출을
'주문 단위'로 중복제거하도록 강제한다. order_fact 는 이 중복제거를 매 조회마다
반복하지 않도록 주문 원장(read model)으로 한 번 머티리얼라이즈한 결과다.

이 모듈은 외부 프레임워크에 의존하지 않는 순수 도메인 로직이다(fold 함수).
영속화/조회는 adapters 계층이 담당한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

# 캠페인 touchpoint 로 간주하는 이벤트 타입.
_TOUCH_TYPES = frozenset({"campaign_impression", "campaign_click", "campaign_open"})


@dataclass(frozen=True)
class OrderEvent:
    """fold 입력. EventRow 또는 OLAP row 에서 어댑터가 변환해 전달한다."""

    visitor_id: str
    order_id: str | None
    type: str
    amount: float
    occurred_at: datetime
    session_id: str | None = None
    utm_medium: str | None = None
    utm_source: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class OrderFact:
    """주문 단위로 중복제거된 사실 1건."""

    tenant_id: str
    order_id: str
    visitor_id: str
    amount: float
    status: str  # "completed". 취소/환불 이벤트 계약 도입 시 "cancelled" 로 확장.
    channel: str
    onsite_matched: bool  # 온사이트 스크립트로 추적된 주문인지(session 동반 여부)
    attributed: bool  # attribution window 내 캠페인 touch 이후 발생 주문인지
    attributed_channel: str | None
    occurred_at: datetime


def _channel(
    utm_medium: str | None, utm_source: str | None, category: str | None
) -> str:
    return utm_medium or utm_source or category or "unknown"


def fold_order_facts(
    tenant_id: str,
    events: Iterable[OrderEvent],
    *,
    attribution_window_hours: int = 24,
) -> list[OrderFact]:
    """행동 이벤트를 주문 단위 사실로 fold 한다.

    - order_id 가 같은 purchase 이벤트는 첫 발생만 채택(amount/channel/visitor 고정).
    - order_id 가 없으면 각 purchase 를 개별 주문으로 취급(synthetic key).
    - onsite_matched: 해당 purchase 이벤트가 session_id 를 동반했는지.
    - attributed: 동일 visitor 가 구매 직전 attribution window 내 캠페인 touch 를
      가졌는지(last-touch 채널 기록).
    """
    touches: dict[str, list[tuple[datetime, str]]] = {}
    purchases: list[OrderEvent] = []
    for event in events:
        if event.type in _TOUCH_TYPES:
            touches.setdefault(event.visitor_id, []).append(
                (event.occurred_at, _channel(event.utm_medium, event.utm_source, event.category))
            )
        elif event.type == "purchase":
            purchases.append(event)

    for items in touches.values():
        items.sort(key=lambda item: item[0])

    window = timedelta(hours=attribution_window_hours)
    facts: dict[str, OrderFact] = {}
    fallback_index = 0
    for event in purchases:
        if event.order_id:
            key = event.order_id
        else:
            key = f"event:{fallback_index}"
            fallback_index += 1
        if key in facts:
            # 주문 단위 중복제거: 같은 order_id 의 추가 purchase 이벤트는 무시.
            continue

        attributed = False
        attributed_channel: str | None = None
        candidates = [
            (touched_at, channel)
            for touched_at, channel in touches.get(event.visitor_id, [])
            if timedelta(0) <= (event.occurred_at - touched_at) <= window
        ]
        if candidates:
            attributed = True
            attributed_channel = max(candidates, key=lambda item: item[0])[1]

        facts[key] = OrderFact(
            tenant_id=tenant_id,
            order_id=key,
            visitor_id=event.visitor_id,
            amount=event.amount,
            status="completed",
            channel=_channel(event.utm_medium, event.utm_source, event.category),
            onsite_matched=event.session_id is not None,
            attributed=attributed,
            attributed_channel=attributed_channel,
            occurred_at=event.occurred_at,
        )
    return list(facts.values())


def revenue_breakdown_from_facts(facts: Iterable[OrderFact]):
    """order_fact 집합에서 매출 breakdown 원시 합계를 산출.

    반환 튜플: (total_revenue, onsite_revenue, attributed_revenue)
    숨은 매출/커버리지는 RevenueBreakdown 의 property 가 파생한다.
    """
    total = 0.0
    onsite = 0.0
    attributed = 0.0
    for fact in facts:
        if fact.status != "completed":
            continue
        total += fact.amount
        if fact.onsite_matched:
            onsite += fact.amount
        if fact.attributed:
            attributed += fact.amount
    return total, onsite, attributed
