"""DB 기반 모델 레지스트리 (prediction/recommendation 공통).

서빙 해석 순서: 테넌트 production 버전(DB) → 패키지 동봉 artifact(global default seed).
promote/rollback 으로 코드 배포 없이 무중단 버전 교체. 파싱/검증 결과는 (model_name,
version) 키로 캐시해 매 요청 재검증을 피하고, promote 로 active 버전이 바뀌면 다음
요청이 자동으로 새 버전을 집어 hot-reload 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import ModelArtifactRow

VALID_STATUSES = ("staging", "production", "archived")


@dataclass(frozen=True)
class ModelVersion:
    tenant_id: str
    model_name: str
    version: str
    status: str
    artifact: dict[str, Any]
    metrics: dict[str, Any]
    created_at: datetime
    promoted_at: datetime | None


class ModelRegistryStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _insert(self):
        bind = self._session.get_bind()
        return sqlite_insert if bind.dialect.name == "sqlite" else pg_insert

    async def register(
        self,
        tenant_id: str,
        model_name: str,
        version: str,
        artifact: dict[str, Any],
        metrics: dict[str, Any],
        *,
        status: str = "staging",
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        values = {
            "tenant_id": tenant_id,
            "model_name": model_name,
            "version": version,
            "status": status,
            "artifact_json": json.dumps(artifact, ensure_ascii=False),
            "metrics_json": json.dumps(metrics, ensure_ascii=False),
            "created_at": now,
        }
        stmt = self._insert()(ModelArtifactRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "model_name", "version"],
            set_={
                "artifact_json": values["artifact_json"],
                "metrics_json": values["metrics_json"],
                "status": status,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def active(self, tenant_id: str, model_name: str) -> ModelVersion | None:
        """테넌트의 production 버전(없으면 None → 호출측이 패키지 seed 로 폴백)."""
        row = (
            await self._session.execute(
                select(ModelArtifactRow).where(
                    ModelArtifactRow.tenant_id == tenant_id,
                    ModelArtifactRow.model_name == model_name,
                    ModelArtifactRow.status == "production",
                )
            )
        ).scalar_one_or_none()
        return self._to_version(row) if row is not None else None

    async def promote(self, tenant_id: str, model_name: str, version: str) -> bool:
        """version 을 production 으로, 기존 production 은 archived 로(무중단 교체/롤백)."""
        target = (
            await self._session.execute(
                select(ModelArtifactRow).where(
                    ModelArtifactRow.tenant_id == tenant_id,
                    ModelArtifactRow.model_name == model_name,
                    ModelArtifactRow.version == version,
                )
            )
        ).scalar_one_or_none()
        if target is None:
            return False
        await self._session.execute(
            update(ModelArtifactRow)
            .where(
                ModelArtifactRow.tenant_id == tenant_id,
                ModelArtifactRow.model_name == model_name,
                ModelArtifactRow.status == "production",
            )
            .values(status="archived")
        )
        await self._session.execute(
            update(ModelArtifactRow)
            .where(ModelArtifactRow.id == target.id)
            .values(status="production", promoted_at=datetime.now(tz=timezone.utc))
        )
        await self._session.commit()
        return True

    async def list_versions(
        self, tenant_id: str, model_name: str
    ) -> list[ModelVersion]:
        rows = (
            await self._session.execute(
                select(ModelArtifactRow)
                .where(
                    ModelArtifactRow.tenant_id == tenant_id,
                    ModelArtifactRow.model_name == model_name,
                )
                .order_by(ModelArtifactRow.id)
            )
        ).scalars().all()
        return [self._to_version(row) for row in rows]

    @staticmethod
    def _to_version(row: ModelArtifactRow) -> ModelVersion:
        return ModelVersion(
            tenant_id=row.tenant_id,
            model_name=row.model_name,
            version=row.version,
            status=row.status,
            artifact=json.loads(row.artifact_json),
            metrics=json.loads(row.metrics_json),
            created_at=row.created_at,
            promoted_at=row.promoted_at,
        )


# (model_name, version) -> 파싱·검증된 artifact. 버전 키라 promote 시 자동 무효화.
_parsed_cache: dict[tuple[str, str], Any] = {}


def parse_cached(model_name: str, version: str, raw: dict[str, Any], parser: Callable[[dict], Any]):
    key = (model_name, version)
    cached = _parsed_cache.get(key)
    if cached is None:
        cached = parser(raw)
        _parsed_cache[key] = cached
    return cached


def reset_parsed_cache() -> None:
    _parsed_cache.clear()
