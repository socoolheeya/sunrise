#!/usr/bin/env bash
set -euo pipefail

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CLICKHOUSE_DB="${SUNRISE_CLICKHOUSE_DB:?Set SUNRISE_CLICKHOUSE_DB}"
CLICKHOUSE_USER="${SUNRISE_CLICKHOUSE_USER:?Set SUNRISE_CLICKHOUSE_USER}"
CLICKHOUSE_PASSWORD="${SUNRISE_CLICKHOUSE_PASSWORD:?Set SUNRISE_CLICKHOUSE_PASSWORD}"

query() {
  local title="$1"
  local sql="$2"
  printf '\n### %s\n' "$title"
  docker compose exec -T clickhouse clickhouse-client \
    --user "${CLICKHOUSE_USER}" \
    --password "${CLICKHOUSE_PASSWORD}" \
    --database "${CLICKHOUSE_DB}" \
    --query "${sql}"
}

query "ClickHouse database" "SELECT currentDatabase()"
query "Physical tables and materialized views" \
  "SELECT name, engine FROM system.tables WHERE database = currentDatabase() ORDER BY name"
query "Kafka source topic" \
  "SELECT name, engine FROM system.tables WHERE database = currentDatabase() AND engine = 'Kafka'"
query "Events row count" "SELECT count() FROM events"
