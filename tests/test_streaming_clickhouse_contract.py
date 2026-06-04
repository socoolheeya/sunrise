"""Streaming ingestion contract checks for compose ClickHouse/Kafka wiring."""

from __future__ import annotations

from pathlib import Path

from app.core.clickhouse_migrations import apply_clickhouse_migrations
from app.core.config import Settings


def test_clickhouse_init_defines_kafka_to_deduped_events_pipeline():
    ddl = Path("clickhouse/init/001_events.sql").read_text(encoding="utf-8")

    assert "ENGINE = Kafka" in ddl
    assert "kafka_broker_list = 'redpanda:29092'" in ddl
    assert "kafka_topic_list = 'raw.events'" in ddl
    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_events" in ddl
    assert "TO sunrise.events" in ddl
    assert "WHERE schema_version = 'tracking-event.v1'" in ddl
    assert "CREATE TABLE IF NOT EXISTS sunrise.agg_metric_daily_v2" in ddl
    assert "raw_events_to_agg_metric_daily_v2" in ddl
    assert "CREATE TABLE IF NOT EXISTS sunrise.visitor_features_daily_v2" in ddl
    assert "raw_events_to_visitor_features_daily_v2" in ddl
    assert "CREATE TABLE IF NOT EXISTS sunrise.product_stats_daily_v2" in ddl
    assert "raw_events_to_product_stats_daily_v2" in ddl
    assert "CREATE TABLE IF NOT EXISTS sunrise.product_features_v1" in ddl
    assert "ENGINE = ReplacingMergeTree(updated_at)" in ddl
    assert "CREATE TABLE IF NOT EXISTS sunrise.visitor_product_signals_daily_v2" in ddl
    assert "raw_events_to_visitor_product_signals_daily_v2" in ddl


def test_compose_uses_kafka_ingestion_and_disables_sql_clickhouse_mirror():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "SUNRISE_INGESTION_SINK: kafka" in compose
    assert 'SUNRISE_CLICKHOUSE_MIRROR_INGESTION: "false"' in compose
    assert "SUNRISE_KAFKA_BOOTSTRAP_SERVERS: redpanda:29092" in compose
    assert "SUNRISE_CLICKHOUSE_METRIC_DAILY_TABLE" in compose
    assert "SUNRISE_CLICKHOUSE_VISITOR_FEATURES_TABLE" in compose
    assert "SUNRISE_CLICKHOUSE_PRODUCT_STATS_TABLE" in compose
    assert "SUNRISE_CLICKHOUSE_PRODUCT_FEATURES_TABLE" in compose
    assert "SUNRISE_CLICKHOUSE_VISITOR_PRODUCT_SIGNALS_TABLE" in compose
    assert "rpk topic create raw.events --partitions 12" in compose
    assert "condition: service_completed_successfully" in compose
    assert "demo-key" not in compose
    assert "demo-tenant" not in compose
    assert "${SUNRISE_API_KEYS_JSON:?Set SUNRISE_API_KEYS_JSON}" in compose


def test_dockerignore_excludes_local_state_and_secret_env_files():
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    assert "venv" in dockerignore
    assert ".venv" in dockerignore
    assert "tests" in dockerignore
    assert "docs" in dockerignore


class FakeMigrationClient:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, sql: str):
        self.commands.append(sql)


async def test_clickhouse_migration_helper_applies_read_model_ddl():
    client = FakeMigrationClient()
    settings = Settings(
        clickhouse_events_table="analytics.events",
        kafka_bootstrap_servers="broker:9092",
        kafka_raw_events_topic="tenant.raw.events",
    )

    await apply_clickhouse_migrations(client, settings)

    joined = "\n".join(client.commands)
    assert "CREATE DATABASE IF NOT EXISTS analytics" in joined
    assert "analytics.raw_events_queue" in joined
    assert "broker:9092" in joined
    assert "'tenant.raw.events'" in joined
    assert "analytics.visitor_features_daily_v2" in joined
    assert "analytics.product_features_v1" in joined
