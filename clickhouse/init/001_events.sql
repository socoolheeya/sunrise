CREATE DATABASE IF NOT EXISTS sunrise;

CREATE TABLE IF NOT EXISTS sunrise.events
(
    tenant_id LowCardinality(String),
    event_id String,
    visitor_id String,
    type LowCardinality(String),
    product_id Nullable(String),
    category Nullable(String),
    session_id Nullable(String),
    order_id Nullable(String),
    utm_source Nullable(String),
    utm_medium Nullable(String),
    utm_campaign Nullable(String),
    landing_page Nullable(String),
    amount Nullable(Float64),
    occurred_at DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(received_at)
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (tenant_id, event_id);

CREATE TABLE IF NOT EXISTS sunrise.raw_events_queue
(
    schema_version String,
    tenant_id String,
    event_id String,
    visitor_id String,
    type String,
    product_id Nullable(String),
    category Nullable(String),
    session_id Nullable(String),
    order_id Nullable(String),
    utm_source Nullable(String),
    utm_medium Nullable(String),
    utm_campaign Nullable(String),
    landing_page Nullable(String),
    amount Nullable(Float64),
    occurred_at DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC')
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'redpanda:29092',
    kafka_topic_list = 'raw.events',
    kafka_group_name = 'sunrise-clickhouse-events-v1',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 2,
    kafka_thread_per_consumer = 1,
    kafka_skip_broken_messages = 1000;

CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_events
TO sunrise.events AS
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
FROM sunrise.raw_events_queue
WHERE schema_version = 'tracking-event.v1';

CREATE TABLE IF NOT EXISTS sunrise.agg_metric_daily_v2
(
    tenant_id LowCardinality(String),
    period Date,
    visitor_state AggregateFunction(uniq, String),
    purchaser_state AggregateFunction(uniq, String),
    purchase_count SimpleAggregateFunction(sum, UInt64),
    revenue SimpleAggregateFunction(sum, Float64)
)
ENGINE = AggregatingMergeTree
ORDER BY (tenant_id, period);

CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_agg_metric_daily_v2
TO sunrise.agg_metric_daily_v2 AS
SELECT
    tenant_id,
    toDate(occurred_at) AS period,
    uniqState(visitor_id) AS visitor_state,
    uniqIfState(visitor_id, type = 'purchase') AS purchaser_state,
    countIf(type = 'purchase') AS purchase_count,
    sumIf(ifNull(amount, 0), type = 'purchase') AS revenue
FROM sunrise.raw_events_queue
WHERE schema_version = 'tracking-event.v1'
GROUP BY tenant_id, period;

CREATE TABLE IF NOT EXISTS sunrise.visitor_features_daily_v2
(
    tenant_id LowCardinality(String),
    visitor_id String,
    period Date,
    view_count SimpleAggregateFunction(sum, UInt64),
    cart_add_count SimpleAggregateFunction(sum, UInt64),
    purchase_count SimpleAggregateFunction(sum, UInt64),
    revenue SimpleAggregateFunction(sum, Float64),
    last_seen_at SimpleAggregateFunction(max, DateTime64(3, 'UTC')),
    last_purchase_at SimpleAggregateFunction(max, DateTime64(3, 'UTC'))
)
ENGINE = AggregatingMergeTree
ORDER BY (tenant_id, visitor_id, period);

CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_visitor_features_daily_v2
TO sunrise.visitor_features_daily_v2 AS
SELECT
    tenant_id,
    visitor_id,
    toDate(occurred_at) AS period,
    countIf(type = 'view') AS view_count,
    countIf(type = 'cart_add') AS cart_add_count,
    countIf(type = 'purchase') AS purchase_count,
    sumIf(ifNull(amount, 0), type = 'purchase') AS revenue,
    max(occurred_at) AS last_seen_at,
    maxIf(occurred_at, type = 'purchase') AS last_purchase_at
FROM sunrise.raw_events_queue
WHERE schema_version = 'tracking-event.v1'
GROUP BY tenant_id, visitor_id, period;

CREATE TABLE IF NOT EXISTS sunrise.product_stats_daily_v2
(
    tenant_id LowCardinality(String),
    product_id String,
    period Date,
    category_state AggregateFunction(anyLast, Nullable(String)),
    view_count SimpleAggregateFunction(sum, UInt64),
    cart_add_count SimpleAggregateFunction(sum, UInt64),
    purchase_count SimpleAggregateFunction(sum, UInt64),
    buyer_state AggregateFunction(uniq, String)
)
ENGINE = AggregatingMergeTree
ORDER BY (tenant_id, product_id, period);

CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_product_stats_daily_v2
TO sunrise.product_stats_daily_v2 AS
SELECT
    tenant_id,
    product_id,
    toDate(occurred_at) AS period,
    anyLastState(category) AS category_state,
    countIf(type = 'view') AS view_count,
    countIf(type = 'cart_add') AS cart_add_count,
    countIf(type = 'purchase') AS purchase_count,
    uniqIfState(visitor_id, type = 'purchase') AS buyer_state
FROM sunrise.raw_events_queue
WHERE schema_version = 'tracking-event.v1'
  AND product_id IS NOT NULL
GROUP BY tenant_id, product_id, period;

CREATE TABLE IF NOT EXISTS sunrise.product_features_v1
(
    tenant_id LowCardinality(String),
    product_id String,
    category Nullable(String),
    price Nullable(Float64),
    original_price Nullable(Float64),
    gross_margin Nullable(Float64),
    rating Nullable(Float64),
    review_count Nullable(UInt64),
    return_rate Nullable(Float64),
    in_stock Bool,
    updated_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (tenant_id, product_id);

CREATE TABLE IF NOT EXISTS sunrise.visitor_product_signals_daily_v2
(
    tenant_id LowCardinality(String),
    visitor_id String,
    product_id Nullable(String),
    category Nullable(String),
    key String,
    period Date,
    view_count SimpleAggregateFunction(sum, UInt64),
    cart_add_count SimpleAggregateFunction(sum, UInt64),
    purchase_count SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
ORDER BY (tenant_id, visitor_id, key, period);

CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_visitor_product_signals_daily_v2
TO sunrise.visitor_product_signals_daily_v2 AS
SELECT
    tenant_id,
    visitor_id,
    product_id,
    category,
    coalesce(product_id, category) AS key,
    toDate(occurred_at) AS period,
    countIf(type = 'view') AS view_count,
    countIf(type = 'cart_add') AS cart_add_count,
    countIf(type = 'purchase') AS purchase_count
FROM sunrise.raw_events_queue
WHERE schema_version = 'tracking-event.v1'
  AND coalesce(product_id, category) IS NOT NULL
GROUP BY tenant_id, visitor_id, product_id, category, key, period;
