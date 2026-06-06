"""Kafka 수집 모드 테스트.

외부 Kafka 없이 FakeProducer 로 Kafka 어댑터 계약과 HTTP 와이어링을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient

from app.core.observability import IngestionMetrics
from app.ingestion.adapters.kafka import (
    KafkaEventRepository,
    KafkaPublishError,
    event_payload,
)
from app.ingestion.domain.model import TrackingEvent

NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


class FakeProducer:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.stopped = False

    async def send_and_wait(self, topic, value, key=None, headers=None):
        self.messages.append(
            {"topic": topic, "value": value, "key": key, "headers": headers}
        )

    async def stop(self):
        self.stopped = True


class FailingProducer(FakeProducer):
    def __init__(self, fail_topics: set[str]) -> None:
        super().__init__()
        self.fail_topics = fail_topics

    async def send_and_wait(self, topic, value, key=None, headers=None):
        if topic in self.fail_topics:
            raise RuntimeError(f"{topic} unavailable")
        await super().send_and_wait(topic, value, key=key, headers=headers)


class TransientFailingProducer(FakeProducer):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def send_and_wait(self, topic, value, key=None, headers=None):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("temporary broker failure")
        await super().send_and_wait(topic, value, key=key, headers=headers)


def _tracking_event(event_id: str, visitor_id: str = "v1") -> TrackingEvent:
    return TrackingEvent.create(
        tenant_id="tenant-a",
        event_id=event_id,
        visitor_id=visitor_id,
        type="view",
        occurred_at=NOW,
        received_at=NOW,
        session_id="s1",
        utm_source="naver",
        utm_medium="cpc",
        utm_campaign="summer",
        landing_page="https://shop.example/landing",
    )


def test_event_payload_includes_published_language_version():
    payload = event_payload(_tracking_event("e1"))

    assert payload["schema_version"] == "tracking-event.v1"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["event_id"] == "e1"
    assert payload["session_id"] == "s1"
    assert payload["utm_source"] == "naver"
    assert payload["utm_medium"] == "cpc"
    assert payload["occurred_at"] == "2026-06-03 00:00:00.000"


async def test_kafka_repository_publishes_unique_events_only():
    producer = FakeProducer()
    metrics = IngestionMetrics()
    repo = KafkaEventRepository(producer, "raw.events", metrics=metrics)

    result = await repo.save_batch([
        _tracking_event("e1", "v1"),
        _tracking_event("e1", "v1"),
        _tracking_event("e2", "v2"),
    ])

    assert result.accepted == 2
    assert result.duplicates == 1
    assert len(producer.messages) == 2
    first = producer.messages[0]
    assert first["topic"] == "raw.events"
    assert first["key"] == b"tenant-a:v1"
    assert ("schema_version", b"tracking-event.v1") in first["headers"]
    assert json.loads(first["value"])["event_id"] == "e1"
    assert metrics.accepted_events == 2
    assert metrics.duplicate_events == 1


async def test_kafka_repository_retries_transient_publish_failure():
    producer = TransientFailingProducer()
    metrics = IngestionMetrics()
    repo = KafkaEventRepository(
        producer,
        "raw.events",
        publish_attempts=2,
        metrics=metrics,
    )

    result = await repo.save_batch([_tracking_event("e1")])

    assert result.accepted == 1
    assert producer.attempts == 2
    assert len(producer.messages) == 1
    assert metrics.publish_failures == 0
    assert metrics.dlq_published == 0


async def test_kafka_repository_publishes_dlq_on_raw_publish_failure():
    producer = FailingProducer(fail_topics={"raw.events"})
    metrics = IngestionMetrics()
    repo = KafkaEventRepository(
        producer,
        "raw.events",
        dlq_topic="raw.events.dlq",
        metrics=metrics,
    )

    try:
        await repo.save_batch([_tracking_event("e1")])
    except KafkaPublishError:
        pass
    else:
        raise AssertionError("expected KafkaPublishError")

    assert metrics.publish_failures == 1
    assert metrics.dlq_published == 1
    assert metrics.dlq_failures == 0
    assert len(producer.messages) == 1
    dlq = producer.messages[0]
    assert dlq["topic"] == "raw.events.dlq"
    payload = json.loads(dlq["value"])
    assert payload["failure_type"] == "kafka_publish_failed"
    assert payload["event"]["event_id"] == "e1"


async def test_kafka_repository_tracks_dlq_failure_without_hiding_raw_failure():
    producer = FailingProducer(fail_topics={"raw.events", "raw.events.dlq"})
    metrics = IngestionMetrics()
    repo = KafkaEventRepository(
        producer,
        "raw.events",
        dlq_topic="raw.events.dlq",
        metrics=metrics,
    )

    try:
        await repo.save_batch([_tracking_event("e1")])
    except KafkaPublishError:
        pass
    else:
        raise AssertionError("expected KafkaPublishError")

    assert metrics.publish_failures == 1
    assert metrics.dlq_published == 0
    assert metrics.dlq_failures == 1
    assert producer.messages == []


async def test_kafka_repository_can_disable_dlq():
    producer = FailingProducer(fail_topics={"raw.events"})
    metrics = IngestionMetrics()
    repo = KafkaEventRepository(
        producer,
        "raw.events",
        dlq_topic=None,
        metrics=metrics,
    )

    try:
        await repo.save_batch([_tracking_event("e1")])
    except KafkaPublishError:
        pass
    else:
        raise AssertionError("expected KafkaPublishError")

    assert metrics.publish_failures == 1
    assert metrics.dlq_published == 0
    assert producer.messages == []


async def test_collect_uses_kafka_sink_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv(
        "SUNRISE_API_KEYS",
        '{"test-key": "tenant-a"}',
    )
    monkeypatch.setenv("SUNRISE_INGESTION_SINK", "kafka")
    monkeypatch.setenv("SUNRISE_KAFKA_RAW_EVENTS_TOPIC", "raw.events.test")

    from app.core import cache, config, database
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    await init_models()

    app = create_app()
    fake = FakeProducer()
    app.state.kafka_producer = fake

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.post(
            "/v1/collect",
            json={
                "events": [
                    {"event_id": "e1", "visitor_id": "v1", "type": "view"},
                    {"event_id": "e1", "visitor_id": "v1", "type": "view"},
                ]
            },
        )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert response.json()["duplicates"] == 1
    assert len(fake.messages) == 1
    assert fake.messages[0]["topic"] == "raw.events.test"


async def test_collect_kafka_sink_requires_ready_producer(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv(
        "SUNRISE_API_KEYS",
        '{"test-key": "tenant-a"}',
    )
    monkeypatch.setenv("SUNRISE_INGESTION_SINK", "kafka")

    from app.core import cache, config, database
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    await init_models()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.post(
            "/v1/collect",
            json={"events": [{"event_id": "e1", "visitor_id": "v1", "type": "view"}]},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Kafka producer is not ready"


async def test_collect_kafka_publish_failure_returns_503(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SUNRISE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv(
        "SUNRISE_API_KEYS",
        '{"test-key": "tenant-a"}',
    )
    monkeypatch.setenv("SUNRISE_INGESTION_SINK", "kafka")
    monkeypatch.setenv("SUNRISE_KAFKA_RAW_EVENTS_TOPIC", "raw.events.test")
    monkeypatch.setenv("SUNRISE_KAFKA_DLQ_TOPIC", "raw.events.dlq.test")

    from app.core import cache, config, database, observability
    from app.core.database import init_models
    from app.main import create_app

    config._settings = None
    database.reset_state()
    cache.reset_state()
    observability.reset_state()
    await init_models()

    app = create_app()
    fake = FailingProducer(fail_topics={"raw.events.test"})
    app.state.kafka_producer = fake

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Sunrise-Key": "test-key"},
    ) as client:
        response = await client.post(
            "/v1/collect",
            json={"events": [{"event_id": "e1", "visitor_id": "v1", "type": "view"}]},
        )

    metrics = observability.get_ingestion_metrics()
    assert response.status_code == 503
    assert response.json()["detail"] == "failed to publish event batch"
    assert metrics.publish_failures == 1
    assert metrics.dlq_published == 1
    assert fake.messages[0]["topic"] == "raw.events.dlq.test"
