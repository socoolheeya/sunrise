"""SQLAlchemy feature repository for prediction lite mode."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import EventRow
from app.prediction.domain.model import ProductSignal, VisitorFeatures
from app.prediction.domain.repository import PredictionRepository


class SqlPredictionRepository(PredictionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def visitor_features(
        self, tenant_id: str, visitor_ids: list[str], start: datetime, end: datetime
    ) -> list[VisitorFeatures]:
        if not visitor_ids:
            return []

        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                func.count(case((EventRow.type == "view", 1))).label("view_count"),
                func.count(case((EventRow.type == "cart_add", 1))).label("cart_add_count"),
                func.count(case((EventRow.type == "purchase", 1))).label("purchase_count"),
                func.coalesce(
                    func.sum(case((EventRow.type == "purchase", EventRow.amount), else_=0.0)),
                    0.0,
                ).label("revenue"),
                func.max(EventRow.occurred_at).label("last_seen_at"),
                func.max(
                    case((EventRow.type == "purchase", EventRow.occurred_at))
                ).label("last_purchase_at"),
                func.min(
                    case((EventRow.type == "purchase", EventRow.occurred_at))
                ).label("first_purchase_at"),
            )
            .where(
                and_(
                    EventRow.tenant_id == tenant_id,
                    EventRow.visitor_id.in_(visitor_ids),
                    EventRow.occurred_at >= start,
                    EventRow.occurred_at < end,
                )
            )
            .group_by(EventRow.visitor_id)
        )
        by_visitor = {
            row.visitor_id: VisitorFeatures(
                visitor_id=row.visitor_id,
                view_count=int(row.view_count or 0),
                cart_add_count=int(row.cart_add_count or 0),
                purchase_count=int(row.purchase_count or 0),
                revenue=float(row.revenue or 0.0),
                last_seen_at=row.last_seen_at,
                last_purchase_at=row.last_purchase_at,
                first_purchase_at=row.first_purchase_at,
            )
            for row in rows.all()
        }
        return [
            by_visitor.get(
                visitor_id,
                VisitorFeatures(
                    visitor_id=visitor_id,
                    view_count=0,
                    cart_add_count=0,
                    purchase_count=0,
                    revenue=0.0,
                    last_seen_at=None,
                    last_purchase_at=None,
                ),
            )
            for visitor_id in visitor_ids
        ]

    async def population_features(
        self, tenant_id: str, start: datetime, end: datetime, *, limit: int = 50_000
    ) -> list[VisitorFeatures]:
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                func.count(case((EventRow.type == "view", 1))).label("view_count"),
                func.count(case((EventRow.type == "cart_add", 1))).label("cart_add_count"),
                func.count(case((EventRow.type == "purchase", 1))).label("purchase_count"),
                func.coalesce(
                    func.sum(case((EventRow.type == "purchase", EventRow.amount), else_=0.0)),
                    0.0,
                ).label("revenue"),
                func.max(EventRow.occurred_at).label("last_seen_at"),
                func.max(case((EventRow.type == "purchase", EventRow.occurred_at))).label(
                    "last_purchase_at"
                ),
                func.min(case((EventRow.type == "purchase", EventRow.occurred_at))).label(
                    "first_purchase_at"
                ),
            )
            .where(
                and_(
                    EventRow.tenant_id == tenant_id,
                    EventRow.occurred_at >= start,
                    EventRow.occurred_at < end,
                )
            )
            .group_by(EventRow.visitor_id)
            .having(func.count(case((EventRow.type == "purchase", 1))) > 0)
            .limit(limit)
        )
        return [
            VisitorFeatures(
                visitor_id=row.visitor_id,
                view_count=int(row.view_count or 0),
                cart_add_count=int(row.cart_add_count or 0),
                purchase_count=int(row.purchase_count or 0),
                revenue=float(row.revenue or 0.0),
                last_seen_at=row.last_seen_at,
                last_purchase_at=row.last_purchase_at,
                first_purchase_at=row.first_purchase_at,
            )
            for row in rows.all()
        ]

    async def product_signals(
        self,
        tenant_id: str,
        visitor_id: str,
        keys: list[str] | None,
        start: datetime,
        end: datetime,
    ) -> list[ProductSignal]:
        key_expr = func.coalesce(EventRow.product_id, EventRow.category)
        filters = [
            EventRow.tenant_id == tenant_id,
            EventRow.visitor_id == visitor_id,
            EventRow.occurred_at >= start,
            EventRow.occurred_at < end,
            key_expr.is_not(None),
        ]
        if keys:
            filters.append(key_expr.in_(keys))

        rows = await self._session.execute(
            select(
                key_expr.label("key"),
                func.count(case((EventRow.type == "view", 1))).label("view_count"),
                func.count(case((EventRow.type == "cart_add", 1))).label("cart_add_count"),
                func.count(case((EventRow.type == "purchase", 1))).label("purchase_count"),
            )
            .where(and_(*filters))
            .group_by(key_expr)
        )
        return [
            ProductSignal(
                key=str(row.key),
                view_count=int(row.view_count or 0),
                cart_add_count=int(row.cart_add_count or 0),
                purchase_count=int(row.purchase_count or 0),
            )
            for row in rows.all()
        ]

