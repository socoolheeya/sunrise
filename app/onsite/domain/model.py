"""Domain models for onsite campaign decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OnsiteEventType(str, Enum):
    VIEW = "view"
    CART_ADD = "cart_add"
    EXIT_INTENT = "exit_intent"
    IDLE = "idle"
    PAGE_HIDE = "page_hide"


class OnsitePlacement(str, Enum):
    POPUP = "popup"
    BANNER = "banner"
    WIDGET = "widget"


@dataclass(frozen=True)
class RecentBehavior:
    viewed_product_ids: tuple[str, ...] = ()
    cart_product_ids: tuple[str, ...] = ()
    purchased_product_ids: tuple[str, ...] = ()
    last_event_at: datetime | None = None


@dataclass(frozen=True)
class OnsiteDecisionContext:
    tenant_id: str
    visitor_id: str
    current_event: OnsiteEventType
    page_url: str | None
    product_id: str | None
    category: str | None
    placement: OnsitePlacement
    recent: RecentBehavior
    now: datetime


@dataclass(frozen=True)
class OnsiteRecommendationItem:
    product_id: str
    category: str | None
    score: float
    reason: str


@dataclass(frozen=True)
class OnsiteCreative:
    headline: str
    body: str
    call_to_action: str


@dataclass(frozen=True)
class OnsiteDecision:
    decision_id: str
    campaign_id: str | None
    eligible: bool
    trigger: str | None
    placement: OnsitePlacement
    priority: str | None
    creative: OnsiteCreative | None
    items: tuple[OnsiteRecommendationItem, ...]
    frequency_cap_key: str
    generated_at: datetime
