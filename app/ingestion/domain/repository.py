"""수집 도메인 Port (인터페이스). 구현은 adapters 계층에 위치한다."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.ingestion.domain.model import IngestResult, TrackingEvent


class EventRepository(ABC):
    @abstractmethod
    async def save_batch(self, events: list[TrackingEvent]) -> IngestResult:
        """이벤트 배치를 처리하고 (수락/중복) 수를 반환한다.

        lite 모드 구현은 DB 에 멱등 저장하고, 운영형 구현은 Kafka 로 발행한다.
        """
        raise NotImplementedError
