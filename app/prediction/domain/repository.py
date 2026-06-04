"""Prediction feature repository port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.prediction.domain.model import ProductSignal, VisitorFeatures


class PredictionRepository(ABC):
    @abstractmethod
    async def visitor_features(
        self, tenant_id: str, visitor_ids: list[str], start: datetime, end: datetime
    ) -> list[VisitorFeatures]:
        raise NotImplementedError

    @abstractmethod
    async def product_signals(
        self,
        tenant_id: str,
        visitor_id: str,
        keys: list[str] | None,
        start: datetime,
        end: datetime,
    ) -> list[ProductSignal]:
        raise NotImplementedError

