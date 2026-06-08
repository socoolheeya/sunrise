"""AI 생성물 검토 큐 저장소 (Outbound Adapter)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import AiReviewRow

_VALID_DECISIONS = {"approved", "rejected"}


@dataclass(frozen=True)
class AiReview:
    id: int
    kind: str
    status: str
    payload: dict[str, Any]
    guardrail: dict[str, Any]
    reviewer: str | None
    note: str | None
    created_at: datetime
    decided_at: datetime | None


class AiReviewStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        tenant_id: str,
        kind: str,
        payload: dict[str, Any],
        guardrail: dict[str, Any],
    ) -> int:
        row = AiReviewRow(
            tenant_id=tenant_id,
            kind=kind,
            status="pending",
            payload_json=json.dumps(payload, ensure_ascii=False),
            guardrail_json=json.dumps(guardrail, ensure_ascii=False),
            created_at=datetime.now(tz=timezone.utc),
        )
        self._session.add(row)
        await self._session.commit()
        return row.id

    async def list_reviews(
        self, tenant_id: str, status: str | None, limit: int = 100
    ) -> list[AiReview]:
        stmt = select(AiReviewRow).where(AiReviewRow.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(AiReviewRow.status == status)
        stmt = stmt.order_by(AiReviewRow.id).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_review(row) for row in rows]

    async def decide(
        self,
        tenant_id: str,
        review_id: int,
        decision: str,
        *,
        reviewer: str | None,
        note: str | None,
    ) -> AiReview | None:
        if decision not in _VALID_DECISIONS:
            raise ValueError("decision must be 'approved' or 'rejected'")
        # pending 상태에서만 전이(중복/역전이 방지).
        result = await self._session.execute(
            update(AiReviewRow)
            .where(
                AiReviewRow.tenant_id == tenant_id,
                AiReviewRow.id == review_id,
                AiReviewRow.status == "pending",
            )
            .values(
                status=decision,
                reviewer=reviewer,
                note=note,
                decided_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._session.commit()
        if result.rowcount == 0:
            return None
        row = (
            await self._session.execute(
                select(AiReviewRow).where(
                    AiReviewRow.tenant_id == tenant_id, AiReviewRow.id == review_id
                )
            )
        ).scalar_one()
        return self._to_review(row)

    @staticmethod
    def _to_review(row: AiReviewRow) -> AiReview:
        return AiReview(
            id=row.id,
            kind=row.kind,
            status=row.status,
            payload=json.loads(row.payload_json),
            guardrail=json.loads(row.guardrail_json),
            reviewer=row.reviewer,
            note=row.note,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )
