"""가벼운 애플리케이션 관측성 포트.

Prometheus 같은 외부 exporter 를 붙이기 전에도 핵심 수집 실패/성공 카운터를
테스트하고 확인할 수 있게 in-process 카운터를 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IngestionMetrics:
    accepted_events: int = 0
    duplicate_events: int = 0
    publish_failures: int = 0
    dlq_published: int = 0
    dlq_failures: int = 0

    def record_result(self, *, accepted: int, duplicates: int) -> None:
        self.accepted_events += accepted
        self.duplicate_events += duplicates

    def record_publish_failure(self) -> None:
        self.publish_failures += 1

    def record_dlq_published(self) -> None:
        self.dlq_published += 1

    def record_dlq_failure(self) -> None:
        self.dlq_failures += 1


_ingestion_metrics = IngestionMetrics()


def get_ingestion_metrics() -> IngestionMetrics:
    return _ingestion_metrics


def reset_state() -> None:
    global _ingestion_metrics
    _ingestion_metrics = IngestionMetrics()

