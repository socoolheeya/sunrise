"""행동 이벤트 스키마 (단일 진실 소스).

쇼핑몰 1-script 가 전송하는 이벤트의 계약. 수집 API 의 입력 검증에 사용한다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.events.registry import TRACKING_EVENT_SCHEMA_VERSION


class EventType(str, Enum):
    VIEW = "view"  # 상품 조회
    CART_ADD = "cart_add"  # 장바구니 담기
    CART_REMOVE = "cart_remove"  # 장바구니 빼기
    PURCHASE = "purchase"  # 구매
    CATEGORY_VIEW = "category_view"  # 카테고리 탐색
    CAMPAIGN_IMPRESSION = "campaign_impression"  # 온사이트 캠페인 노출
    CAMPAIGN_CLICK = "campaign_click"  # 온사이트 캠페인 클릭
    CAMPAIGN_DISMISS = "campaign_dismiss"  # 온사이트 캠페인 닫기


class TrackingEventIn(BaseModel):
    """단일 행동 이벤트."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128, description="멱등 키")
    visitor_id: str = Field(min_length=1, max_length=128)
    type: EventType
    product_id: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    order_id: str | None = Field(default=None, max_length=128)
    utm_source: str | None = Field(default=None, max_length=128)
    utm_medium: str | None = Field(default=None, max_length=128)
    utm_campaign: str | None = Field(default=None, max_length=128)
    landing_page: str | None = Field(default=None, max_length=2048)
    amount: float | None = Field(default=None, ge=0, description="구매 금액(구매 이벤트)")
    occurred_at: datetime | None = Field(
        default=None, description="클라이언트 발생 시각(미지정 시 서버 수신 시각)"
    )


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(
        default=TRACKING_EVENT_SCHEMA_VERSION,
        description="수집 이벤트 계약 버전",
    )
    events: list[TrackingEventIn] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: str) -> str:
        if value != TRACKING_EVENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version: {value}; "
                f"expected {TRACKING_EVENT_SCHEMA_VERSION}"
            )
        return value


class CollectResponse(BaseModel):
    schema_version: str = Field(default=TRACKING_EVENT_SCHEMA_VERSION)
    accepted: int = Field(description="신규로 수락된 이벤트 수")
    duplicates: int = Field(description="배치 내부 또는 기존 저장소와 중복된 이벤트 수")
    received_at: datetime = Field(description="서버 수신 시각")
