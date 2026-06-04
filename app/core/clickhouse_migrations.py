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
    sql = sql.replace("CREATE DATABASE IF NOT EXISTS sunrise", f"CREATE DATABASE IF NOT EXISTS {database}")
    sql = sql.replace("redpanda:29092", settings.kafka_bootstrap_servers)
    sql = sql.replace("'raw.events'", f"'{settings.kafka_raw_events_topic}'")

    command = getattr(client, "command", None)
    if command is None:
        return

    for statement in _statements(sql):
        result = command(statement)
        if inspect.isawaitable(result):
            await result
