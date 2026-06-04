"""SQLAlchemy recommendation repository.

events 원본 테이블과 product_features 테이블을 결합해 후보 상품 feature를 만든다.
운영에서는 이 adapter를 feature store/read model adapter로 교체할 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import EventRow, ProductFeatureRow
from app.recommendation.domain.model import ProductStat, VisitorContext
from app.recommendation.domain.repository import RecommendationRepository


class SqlRecommendationRepository(RecommendationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def popular_products(
        self, tenant_id: str, start: datetime, end: datetime, limit: int
    ) -> list[ProductStat]:
        event_category = func.max(EventRow.category).label("event_category")
        view_c = func.count(case((EventRow.type == "view", 1))).label("view_count")
        cart_c = func.count(case((EventRow.type == "cart_add", 1))).label(
            "cart_add_count"
        )
        buy_c = func.count(case((EventRow.type == "purchase", 1))).label(
            "purchase_count"
        )
        buyer_c = func.count(
            func.distinct(case((EventRow.type == "purchase", EventRow.visitor_id)))
        ).label("buyer_count")
        weight = buy_c * 3 + cart_c * 2 + view_c
        category = func.coalesce(ProductFeatureRow.category, event_category)
        category_avg = (
            select(
                ProductFeatureRow.category.label("category"),
                func.avg(ProductFeatureRow.price).label("category_avg_price"),
            )
            .where(
                and_(
                    ProductFeatureRow.tenant_id == tenant_id,
                    ProductFeatureRow.price.is_not(None),
                )
            )
            .group_by(ProductFeatureRow.category)
            .subquery()
        )

        rows = await self._session.execute(
            select(
                EventRow.product_id.label("product_id"),
                category.label("category"),
                view_c,
                cart_c,
                buy_c,
                buyer_c,
                ProductFeatureRow.price,
                ProductFeatureRow.original_price,
                ProductFeatureRow.gross_margin,
                ProductFeatureRow.rating,
                ProductFeatureRow.review_count,
                ProductFeatureRow.return_rate,
                ProductFeatureRow.in_stock,
                category_avg.c.category_avg_price,
            )
            .outerjoin(
                ProductFeatureRow,
                and_(
                    ProductFeatureRow.tenant_id == EventRow.tenant_id,
                    ProductFeatureRow.product_id == EventRow.product_id,
                ),
            )
            .outerjoin(
                category_avg,
                category_avg.c.category == func.coalesce(ProductFeatureRow.category, EventRow.category),
            )
            .where(
                and_(
                    EventRow.tenant_id == tenant_id,
                    EventRow.occurred_at >= start,
                    EventRow.occurred_at < end,
                    EventRow.product_id.is_not(None),
                )
            )
            .group_by(
                EventRow.product_id,
                ProductFeatureRow.category,
                ProductFeatureRow.price,
                ProductFeatureRow.original_price,
                ProductFeatureRow.gross_margin,
                ProductFeatureRow.rating,
                ProductFeatureRow.review_count,
                ProductFeatureRow.return_rate,
                ProductFeatureRow.in_stock,
                category_avg.c.category_avg_price,
            )
            .order_by(weight.desc(), EventRow.product_id)
            .limit(limit)
        )
        return [
            ProductStat(
                product_id=str(row.product_id),
                category=row.category,
                view_count=int(row.view_count or 0),
                cart_add_count=int(row.cart_add_count or 0),
                purchase_count=int(row.purchase_count or 0),
                buyer_count=int(row.buyer_count or 0),
                price=float(row.price) if row.price is not None else None,
                original_price=(
                    float(row.original_price) if row.original_price is not None else None
                ),
                gross_margin=(
                    float(row.gross_margin) if row.gross_margin is not None else None
                ),
                rating=float(row.rating) if row.rating is not None else None,
                review_count=(
                    int(row.review_count) if row.review_count is not None else None
                ),
                return_rate=(
                    float(row.return_rate) if row.return_rate is not None else None
                ),
                category_avg_price=(
                    float(row.category_avg_price)
                    if row.category_avg_price is not None
                    else None
                ),
                in_stock=bool(row.in_stock) if row.in_stock is not None else True,
            )
            for row in rows.all()
        ]

    async def visitor_context(
        self, tenant_id: str, visitor_id: str, start: datetime, end: datetime
    ) -> VisitorContext:
        base = and_(
            EventRow.tenant_id == tenant_id,
            EventRow.visitor_id == visitor_id,
            EventRow.occurred_at >= start,
            EventRow.occurred_at < end,
        )

        viewed = await self._session.execute(
            select(func.distinct(EventRow.product_id)).where(
                and_(base, EventRow.type == "view", EventRow.product_id.is_not(None))
            )
        )
        purchased = await self._session.execute(
            select(func.distinct(EventRow.product_id)).where(
                and_(
                    base, EventRow.type == "purchase", EventRow.product_id.is_not(None)
                )
            )
        )
        categories = await self._session.execute(
            select(func.distinct(EventRow.category)).where(
                and_(base, EventRow.category.is_not(None))
            )
        )

        return VisitorContext(
            visitor_id=visitor_id,
            viewed_product_ids=frozenset(str(v) for (v,) in viewed.all()),
            purchased_product_ids=frozenset(str(v) for (v,) in purchased.all()),
            engaged_categories=frozenset(str(v) for (v,) in categories.all()),
        )

    async def upsert_product_features(self, tenant_id: str, products: list[dict]) -> int:
        now = datetime.now(timezone.utc)
        accepted = 0
        for product in products:
            values = {
                "tenant_id": tenant_id,
                "product_id": product["product_id"],
                "category": product.get("category"),
                "price": product.get("price"),
                "original_price": product.get("original_price"),
                "gross_margin": product.get("gross_margin"),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "return_rate": product.get("return_rate"),
                "in_stock": product.get("in_stock", True),
                "updated_at": now,
            }
            bind = self._session.get_bind()
            if bind.dialect.name == "sqlite":
                stmt = sqlite_insert(ProductFeatureRow).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["tenant_id", "product_id"],
                    set_=values,
                )
                await self._session.execute(stmt)
            else:
                existing = await self._session.scalar(
                    select(ProductFeatureRow).where(
                        and_(
                            ProductFeatureRow.tenant_id == tenant_id,
                            ProductFeatureRow.product_id == product["product_id"],
                        )
                    )
                )
                if existing is None:
                    self._session.add(ProductFeatureRow(**values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
            accepted += 1
        await self._session.commit()
        return accepted
