"""애플리케이션 설정 (Pydantic Settings).

환경변수로 주입하며, 기본값은 docker-compose 의 PostgreSQL(asyncpg)을 사용한다.
테스트는 conftest 에서 SQLite(aiosqlite)로 오버라이드해 외부 인프라 없이 동작한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUNRISE_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "sunrise"
    deployment_env: Literal["development", "test", "production"] = "development"
    # 기본값: docker-compose PostgreSQL(asyncpg). 컨테이너 내부는 host=db 로 주입한다.
    # 테스트는 conftest 에서 SUNRISE_DATABASE_URL 를 SQLite 로 오버라이드한다.
    database_url: str = "postgresql+asyncpg://sunrise:sunrise@localhost:5432/sunrise"

    # API Key -> tenant_id 매핑. 운영에서는 IAM/시크릿으로 대체.
    # 환경변수 SUNRISE_API_KEYS 에 JSON 으로 주입 가능.
    api_keys: dict[str, str] = Field(
        default_factory=lambda: {"demo-key": "demo-tenant"}
    )

    # 1회 수집 요청당 허용 이벤트 수 상한 (백프레셔 보호).
    max_events_per_request: int = 500
    # collect 요청 본문 크기 상한. 큰 payload 는 API gateway 에서도 같은 값으로 제한한다.
    max_collect_payload_bytes: int = 1_048_576
    # tenant 별 collect rate limit. 0 이면 collector 내부 제한을 비활성화한다.
    collect_rate_limit_per_minute: int = 600

    # 수집 이벤트 처리 방식. sql 은 lite/로컬 모드, kafka 는 운영형 스트림 적재 모드.
    ingestion_sink: Literal["sql", "kafka"] = "sql"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_raw_events_topic: str = "raw.events"
    kafka_dlq_topic: str = "raw.events.dlq"
    kafka_dlq_enabled: bool = True
    kafka_publish_attempts: int = 3
    kafka_acks: str = "all"
    kafka_compression_type: str | None = "gzip"
    kafka_linger_ms: int = 5
    kafka_request_timeout_ms: int = 30_000
    kafka_retry_backoff_ms: int = 100
    kafka_max_batch_size: int = 16_384
    # Kafka(+DLQ) 전면 장애 시 이벤트를 outbox 에 보존했다가 복구 후 재발행한다.
    ingestion_outbox_enabled: bool = True
    ingestion_outbox_max_pending: int = 100_000  # backlog 초과 시 backpressure(503)
    ingestion_outbox_relay_batch: int = 500

    # AI 카피 생성 provider. rules 는 결정론적 기본, anthropic 은 LLM(키 설정 시).
    ai_llm_provider: Literal["rules", "anthropic"] = "rules"
    anthropic_api_key: str | None = None
    ai_llm_model: str = "claude-opus-4-8"

    # 분석 조회 백엔드. sql 은 lite/로컬 모드, clickhouse 는 운영형 OLAP 모드.
    analytics_backend: Literal["sql", "clickhouse"] = "sql"
    clickhouse_dsn: str = "clickhouse://localhost:8123/sunrise"
    clickhouse_events_table: str = "events"
    clickhouse_metric_daily_table: str | None = None
    clickhouse_visitor_features_table: str | None = None
    clickhouse_product_stats_table: str | None = None
    clickhouse_product_features_table: str | None = None
    clickhouse_visitor_product_signals_table: str | None = None
    clickhouse_mirror_ingestion: bool = False

    # 캐시. 미설정 시 NullCache(무동작) → Redis 없이도 동작.
    # 예: redis://localhost:6379/0
    redis_url: str | None = None
    cache_ttl_seconds: int = 60

    # 추천 ML 모델 artifact. 미설정 시 패키지에 포함된 기본 artifact 를 사용한다.
    recommendation_model_path: str | None = None
    # Prediction ML 모델 artifact. 미설정 시 패키지에 포함된 기본 artifact 를 사용한다.
    prediction_model_path: str | None = None

    # 기동 시 테이블 자동 생성(lite/로컬/테스트). 운영은 False + Alembic 마이그레이션.
    auto_create_tables: bool = True

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.deployment_env != "production":
            return self

        if self.api_keys.get("demo-key") == "demo-tenant":
            raise ValueError("production must not use the demo API key mapping")
        if self.auto_create_tables:
            raise ValueError("production must use migrations, not auto_create_tables")
        if self.ingestion_sink != "kafka":
            raise ValueError("production ingestion_sink must be kafka")
        if self.analytics_backend != "clickhouse":
            raise ValueError("production analytics_backend must be clickhouse")
        if self.clickhouse_mirror_ingestion:
            raise ValueError("production must ingest ClickHouse through Kafka, not mirror")
        if self.redis_url is None:
            raise ValueError("production must configure Redis cache")
        if self.collect_rate_limit_per_minute <= 0:
            raise ValueError("production collect rate limit must be enabled")
        if self.kafka_publish_attempts < 2:
            raise ValueError("production Kafka publish attempts must be at least 2")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """싱글턴 설정 접근자 (FastAPI Depends 로 주입)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
