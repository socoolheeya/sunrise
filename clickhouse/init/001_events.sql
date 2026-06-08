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

-- 주문 단위 사실(order_fact): order_id 기준 ReplacingMergeTree 로 구매 이벤트를
-- 주문 단위로 중복제거한다. 같은 order_id 의 다중 purchase 이벤트(재시도/분할발송/
-- 스크립트 중복 발화)는 received_at 최신 1건으로 collapse 되어 매출/주문수 과대
-- 집계를 방지한다. onsite_matched 는 구매 이벤트의 session 동반 여부.
-- (attribution 은 touchpoint join 이 필요하므로 serving 시 계산한다.)
CREATE TABLE IF NOT EXISTS sunrise.order_fact_v2
(
    tenant_id LowCardinality(String),
    order_id String,
    visitor_id String,
    amount Float64,
    status LowCardinality(String),
    channel LowCardinality(String),
    onsite_matched Bool,
    occurred_at DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(received_at)
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (tenant_id, order_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS sunrise.raw_events_to_order_fact_v2
TO sunrise.order_fact_v2 AS
SELECT
    tenant_id,
    order_id,
    visitor_id,
    ifNull(amount, 0) AS amount,
    'completed' AS status,
    coalesce(utm_medium, utm_source, category, 'unknown') AS channel,
    session_id IS NOT NULL AS onsite_matched,
    occurred_at,
    received_at
FROM sunrise.raw_events_queue
WHERE schema_version = 'tracking-event.v1'
  AND type = 'purchase'
  AND order_id IS NOT NULL;

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

-- ===========================================================================
-- 분석 read model (refresh 배치가 적재)
-- ---------------------------------------------------------------------------
-- order_fact / segment / cohort 는 attribution·세그먼트 분류·코호트 버킷처럼
-- 블록 단위 스트리밍 MV 로 표현하기 어려운 로직이 필요하다. 따라서 Python
-- refresh 유스케이스가 전체 로직으로 산출해 아래 테이블에 delete-then-insert 로
-- 머티리얼라이즈하고, 조회는 이 테이블만 SELECT 한다(SQL 백엔드와 동일 의미).
-- ReplacingMergeTree(computed_at) 로 재적재 시 최신본을 채택한다.
-- (위의 *_v2 스트리밍 MV 는 향후 네이티브 사전집계 최적화용으로 유지.)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS sunrise.order_facts
(
    tenant_id LowCardinality(String),
    order_id String,
    visitor_id String,
    amount Float64,
    status LowCardinality(String),
    channel LowCardinality(String),
    onsite_matched Bool,
    attributed Bool,
    attributed_channel Nullable(String),
    occurred_at DateTime64(3, 'UTC'),
    computed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (tenant_id, order_id);

CREATE TABLE IF NOT EXISTS sunrise.customer_segment_daily
(
    tenant_id LowCardinality(String),
    customer_id String,
    as_of DateTime64(3, 'UTC'),
    visit_segment LowCardinality(String),
    purchase_segment LowCardinality(String),
    revenue Float64,
    computed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (tenant_id, customer_id, as_of);

CREATE TABLE IF NOT EXISTS sunrise.cohort_retention
(
    tenant_id LowCardinality(String),
    cohort_type LowCardinality(String),
    granularity LowCardinality(String),
    cohort String,
    offset UInt16,
    base_count UInt32,
    retained_count UInt32,
    retention_rate Float64,
    computed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (tenant_id, cohort_type, granularity, cohort, offset);
