"""ClickHouse schema migration helper.

The ClickHouse Docker entrypoint only runs init SQL on an empty volume. The app
also applies the idempotent DDL at startup so existing volumes and production
deployments converge on the required Kafka/read-model schema.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from app.core.config import Settings


def _migration_sql_path() -> Path:
    return Path(__file__).resolve().parents[2] / "clickhouse" / "init" / "001_events.sql"


def _statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


async def apply_clickhouse_migrations(client: Any, settings: Settings) -> None:
    path = _migration_sql_path()
    if not path.exists():
        return

    sql = path.read_text(encoding="utf-8")
    database = settings.clickhouse_events_table.split(".", 1)[0]
    sql = sql.replace("sunrise.", f"{database}.")
    sql = sql.replace(
        "CREATE DATABASE IF NOT EXISTS sunrise",
        f"CREATE DATABASE IF NOT EXISTS {database}",
    )
    sql = sql.replace("redpanda:29092", settings.kafka_bootstrap_servers)
    sql = sql.replace("'raw.events'", f"'{settings.kafka_raw_events_topic}'")

    command = getattr(client, "command", None)
    if command is None:
        return

    for statement in _event_attribution_column_migrations(
        database=database,
        events_table=settings.clickhouse_events_table,
    ):
        try:
            await _run_command(command, statement)
        except Exception:
            # Fresh volumes create the tables later in 001_events.sql. Existing
            # volumes already have the tables and receive the ALTER above.
            pass

    for statement in _raw_queue_recreation_migrations(database=database):
        await _run_command(command, statement)

    for statement in _statements(sql):
        if "raw_events_to_events" in statement:
            continue
        await _run_command(command, statement)

    for statement in _event_attribution_view_drop(database=database):
        await _run_command(command, statement)

    try:
        for statement in _event_attribution_view_migrations(
            database=database,
            events_table=settings.clickhouse_events_table,
        ):
            await _run_command(command, statement)
    except Exception:
        for statement in _legacy_event_view_migrations(
            database=database,
            events_table=settings.clickhouse_events_table,
        ):
            await _run_command(command, statement)


async def _run_command(command: Any, statement: str) -> None:
    result = command(statement)
    if inspect.isawaitable(result):
        await result


def _event_attribution_view_drop(*, database: str) -> list[str]:
    return [f"DROP VIEW IF EXISTS {database}.raw_events_to_events"]


def _raw_queue_recreation_migrations(*, database: str) -> list[str]:
    return [
        f"DROP VIEW IF EXISTS {database}.raw_events_to_visitor_product_signals_daily_v2",
        f"DROP VIEW IF EXISTS {database}.raw_events_to_product_stats_daily_v2",
        f"DROP VIEW IF EXISTS {database}.raw_events_to_visitor_features_daily_v2",
        f"DROP VIEW IF EXISTS {database}.raw_events_to_agg_metric_daily_v2",
        f"DROP VIEW IF EXISTS {database}.raw_events_to_events",
        f"DROP TABLE IF EXISTS {database}.raw_events_queue",
    ]


def _event_attribution_view_migrations(
    *,
    database: str,
    events_table: str,
) -> list[str]:
    return [
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.raw_events_to_events
        TO {events_table} AS
        SELECT
            tenant_id,
            event_id,
            visitor_id,
            type,
            product_id,
            category,
            session_id,
            order_id,
            utm_source,
            utm_medium,
            utm_campaign,
            landing_page,
            amount,
            occurred_at,
            received_at
        FROM {database}.raw_events_queue
        WHERE schema_version = 'tracking-event.v1'
        """,
    ]


def _legacy_event_view_migrations(
    *,
    database: str,
    events_table: str,
) -> list[str]:
    return [
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.raw_events_to_events
        TO {events_table} AS
        SELECT
            tenant_id,
            event_id,
            visitor_id,
            type,
            product_id,
            category,
            amount,
            occurred_at,
            received_at
        FROM {database}.raw_events_queue
        WHERE schema_version = 'tracking-event.v1'
        """,
    ]


def _event_attribution_columns() -> list[str]:
    return [
        "session_id Nullable(String)",
        "order_id Nullable(String)",
        "utm_source Nullable(String)",
        "utm_medium Nullable(String)",
        "utm_campaign Nullable(String)",
        "landing_page Nullable(String)",
    ]


def _event_attribution_column_migrations(
    *,
    database: str,
    events_table: str,
) -> list[str]:
    statements = [
        f"ALTER TABLE {events_table} ADD COLUMN IF NOT EXISTS {column}"
        for column in _event_attribution_columns()
    ]
    statements.extend(
        f"ALTER TABLE {database}.raw_events_queue ADD COLUMN IF NOT EXISTS {column}"
        for column in _event_attribution_columns()
    )
    return statements
