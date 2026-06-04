"""Recommendation feature repository port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.recommendation.domain.model import ProductStat, VisitorContext


class RecommendationRepository(ABC):
    @abstractmethod
    async def popular_products(
        self, tenant_id: str, start: datetime, end: datetime, limit: int
    ) -> list[ProductStat]:
        """tenant 범위에서 인기 상품 후보 풀을 인기순으로 반환한다."""
        raise NotImplementedError

    @abstractmethod
    async def visitor_context(
        self, tenant_id: str, visitor_id: str, start: datetime, end: datetime
    ) -> VisitorContext:
        """제외/개인화에 필요한 visitor 의 조회/구매/관심 카테고리를 반환한다."""
        raise NotImplementedError

    @abstractmethod
    async def upsert_product_features(self, tenant_id: str, products: list[dict]) -> int:
        """추천 ranker 가 쓰는 상품 가치/품질 feature를 최신값으로 저장한다."""
        raise NotImplementedError
