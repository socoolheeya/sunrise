"""유스케이스: 행동 이벤트 수집.

Port(EventRepository)에만 의존하므로 인메모리 Fake 로 단위 테스트가 가능하다.
"""

from __future__ import annotations

from app.ingestion.domain.model import IngestResult, TrackingEvent
from app.ingestion.domain.repository import EventRepository


class CollectEvents:
    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    async def execute(self, events: list[TrackingEvent]) -> IngestResult:
        if not events:
            return IngestResult(accepted=0, duplicates=0)
        return await self._repository.save_batch(events)
