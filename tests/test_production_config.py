"""Production configuration guardrails."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _production_settings(**overrides):
    values = {
        "deployment_env": "production",
        "api_keys": {"prod-key": "tenant-a"},
        "auto_create_tables": False,
        "ingestion_sink": "kafka",
        "analytics_backend": "clickhouse",
        "clickhouse_mirror_ingestion": False,
        "redis_url": "redis://redis:6379/0",
        "collect_rate_limit_per_minute": 600,
        "kafka_publish_attempts": 3,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_accept_operational_defaults():
    settings = _production_settings()

    assert settings.deployment_env == "production"
    assert settings.ingestion_sink == "kafka"
    assert settings.analytics_backend == "clickhouse"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"api_keys": {"demo-key": "demo-tenant"}}, "demo API key"),
        ({"auto_create_tables": True}, "auto_create_tables"),
        ({"ingestion_sink": "sql"}, "ingestion_sink"),
        ({"analytics_backend": "sql"}, "analytics_backend"),
        ({"clickhouse_mirror_ingestion": True}, "mirror"),
        ({"redis_url": None}, "Redis"),
        ({"collect_rate_limit_per_minute": 0}, "rate limit"),
        ({"kafka_publish_attempts": 1}, "publish attempts"),
    ],
)
def test_production_settings_reject_non_operational_configuration(override, message):
    with pytest.raises(ValidationError) as exc:
        _production_settings(**override)

    assert message in str(exc.value)
