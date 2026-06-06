"""수집 도메인 모델 (순수 — 외부 의존 0).

비즈니스 규칙(불변식)을 팩토리에서 강제하므로 인프라 없이 단위 테스트가 가능하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TrackingEvent:
    tenant_id: str
    event_id: str
    visitor_id: str
    type: str
    occurred_at: datetime
    received_at: datetime
    product_id: str | None = None
    category: str | None = None
    session_id: str | None = None
    order_id: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    landing_page: str | None = None
    amount: float | None = None

    @staticmethod
    def create(
        *,
        tenant_id: str,
        event_id: str,
        visitor_id: str,
        type: str,
        occurred_at: datetime,
        received_at: datetime,
        product_id: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        order_id: str | None = None,
        utm_source: str | None = None,
        utm_medium: str | None = None,
        utm_campaign: str | None = None,
        landing_page: str | None = None,
        amount: float | None = None,
    ) -> "TrackingEvent":
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not event_id:
            raise ValueError("event_id is required")
        if not visitor_id:
            raise ValueError("visitor_id is required")
        if amount is not None and amount < 0:
            raise ValueError("amount must be non-negative")
        if type == "purchase" and amount is None:
            raise ValueError("purchase event requires amount")
        return TrackingEvent(
            tenant_id=tenant_id,
            event_id=event_id,
            visitor_id=visitor_id,
            type=type,
            occurred_at=occurred_at,
            received_at=received_at,
            product_id=product_id,
            category=category,
            session_id=session_id,
            order_id=order_id,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            landing_page=landing_page,
            amount=amount,
        )


@dataclass(frozen=True)
class IngestResult:
    accepted: int
    duplicates: int
