# Python/FastAPI 개발 PRD

## 1. 목적

Python 서비스는 Sunrise 플랫폼의 데이터 수집, 분석, 지표, AI/ML, 추천을 담당한다. 현재 저장소의 FastAPI lite 구현을 운영형 데이터 플랫폼으로 확장하면서도 Clean Architecture 계층을 유지한다.

## 2. 범위

### 2.1 포함

- 행동 이벤트 수집 API
- 이벤트 스키마 검증 및 멱등 처리
- 분석/지표 API: metrics, funnel, cohort, benchmark
- Redis cache를 활용한 read API 성능 개선
- Kafka producer/consumer adapter
- ClickHouse 기반 OLAP repository
- 구매 가능성/이탈/상품 반응 scoring API
- 상품 추천 candidate/ranking API
- AI Agent 진단 API와 캠페인 제안 API
- AI 카피 생성 API
- 온사이트 캠페인 decision API와 노출/클릭/닫기 수집 API
- 50+ 기본 오디언스 템플릿 catalog API
- 테스트, 마이그레이션, 운영 관측성

### 2.2 제외

- 캠페인 상태 관리
- 메시지 발송 job orchestration
- 카카오/문자/이메일 provider 직접 발송
- 결제, 구독, 환불, 정산
- 관리자 권한/승인 workflow

위 제외 범위는 `docs/prd_kotlin.md`에서 다룬다.

## 3. 사용자와 Use Case

| 사용자 | 요구 |
|---|---|
| 쇼핑몰 스크립트 | 행동 이벤트를 낮은 지연으로 안정 전송 |
| 마케터 Console | 매출, CVR, AOV, 퍼널, 코호트를 빠르게 조회 |
| Campaign 서비스 | 세그먼트/캠페인 실행에 필요한 스코어와 추천 결과 조회 |
| AI Agent | 사이트 상태를 진단하고 실행 가능한 캠페인 제안을 생성 |
| 데이터 엔지니어 | 이벤트 계약과 집계 파이프라인을 재현 가능하게 운영 |

## 4. 기능 요구사항

### 4.1 Ingestion

- `POST /v1/collect`는 batch 이벤트를 받는다.
- `X-Sunrise-Key`로 tenant를 식별한다.
- 요청 payload의 tenant 값은 신뢰하지 않고 인증 컨텍스트 tenant를 사용한다.
- 이벤트마다 `event_id`, `visitor_id`, `type`, `occurred_at`을 검증한다.
- 중복 `event_id`는 저장하지 않고 duplicate count로 응답한다.
- 운영 모드에서는 DB 저장 대신 Kafka `raw.events`로 발행한다.
- Kafka 발행 실패 시 retry 후 실패 metric과 DLQ 또는 buffer 정책을 적용한다.

### 4.2 Analytics/Metrics

- `/v1/analytics/metrics`: revenue, sessions, CVR, AOV, repeat rate를 반환한다.
- `/v1/analytics/funnel`: view -> cart_add -> purchase 단계별 visitor count와 drop-off를 반환한다.
- `/v1/analytics/cohort`: 첫 구매월 기준 재구매 retention matrix를 반환한다.
- `/v1/analytics/benchmark`: tenant 지표와 platform/industry 평균을 비교한다.
- 조회 기간은 기본 day boundary로 정렬해 cache hit ratio를 높인다.
- 모든 query는 tenant filter를 강제한다.

### 4.3 Prediction

- `/v1/predictions/purchase-score`는 visitor/customer별 구매 가능성 점수를 반환한다.
- `/v1/predictions/churn-risk`는 구매주기 기반 이탈 위험과 추천 리마케팅 시점을 반환한다.
- `/v1/predictions/product-affinity`는 고객-상품/카테고리 반응 점수를 반환한다.
- 모델 버전, feature version, generated_at을 응답에 포함한다.

### 4.4 Recommendation

- `/v1/recommendations/items`는 visitor/customer 컨텍스트로 추천 상품 목록을 반환한다.
- 이미 본 상품, 이미 구매한 상품, 품절 상품을 제외할 수 있어야 한다.
- candidate generation과 ranking을 분리한다.
- 위젯/메시지/온사이트 등 caller별 placement policy를 입력받는다.
- 운영형 ranking feature에는 CRM 행동 신호뿐 아니라 가격, 할인율, 카테고리 평균가 대비 가격, 평점, 리뷰 수, 반품률, 마진, 재고 같은 상품 가치/품질 신호를 포함한다.

### 4.5 AI Agent/Copy

- `/v1/ai/diagnoses/site`는 지표/퍼널/코호트를 분석해 문제 구간을 반환한다.
- `/v1/ai/suggestions/campaigns`는 audience, channel, message goal을 포함한 캠페인 제안을 반환한다.
- `/v1/ai/copy`는 브랜드 톤, 상품 이미지/텍스트, 캠페인 목적을 입력받아 카피 후보를 생성한다.
- 생성 결과는 guardrail result와 human review 필요 여부를 포함한다.

### 4.6 Onsite Campaign

- `/v1/onsite/decide`는 visitor의 현재 행동 타이밍과 최근 행동 맥락을 평가해 온사이트 팝업/배너/위젯 노출 여부를 반환한다.
- 탐색 보조, 장바구니 회복, 이탈 의도 같은 trigger를 구분한다.
- decision 응답은 campaign_id, decision_id, placement, creative, 추천 상품, frequency cap key를 포함한다.
- `/v1/onsite/impressions`, `/v1/onsite/clicks`, `/v1/onsite/dismissals`는 온사이트 노출/상호작용을 tracking event로 수집한다.
- 실제 운영에서는 Kotlin campaign 서비스의 활성 캠페인, audience membership, priority, frequency cap, experiment group과 연동해야 한다.

### 4.7 Audience Template

- `/v1/audiences/templates`는 이커머스 표준 오디언스 템플릿 50개 이상을 반환한다.
- 템플릿은 category, description, 조건 DSL rule, 추천 채널, 추천 trigger, tag를 포함한다.
- category와 query로 필터링할 수 있어야 한다.
- `/v1/audiences/templates/{template_id}`는 단일 템플릿 상세 조건 계약을 반환한다.
- 운영 확장 시 템플릿 catalog는 관리자 편집형 저장소와 audience preview/materialization으로 연결한다.

## 5. API 초안

| Method | Path | 설명 |
|---|---|---|
| POST | `/v1/collect` | 행동 이벤트 수집 |
| GET | `/v1/analytics/metrics` | 핵심 지표 |
| GET | `/v1/analytics/funnel` | 퍼널 |
| GET | `/v1/analytics/cohort` | 코호트 |
| GET | `/v1/analytics/benchmark` | 벤치마크 |
| POST | `/v1/predictions/purchase-score` | 구매 가능성 |
| POST | `/v1/predictions/churn-risk` | 이탈 위험 |
| POST | `/v1/predictions/product-affinity` | 상품/카테고리 반응 점수 |
| POST | `/v1/recommendations/products` | 추천 상품 가치/품질 feature 업서트 |
| POST | `/v1/recommendations/items` | 상품 추천 |
| POST | `/v1/ai/diagnoses/site` | 사이트 진단 |
| POST | `/v1/ai/suggestions/campaigns` | 캠페인 제안 |
| POST | `/v1/ai/copy` | AI 카피 생성 |
| POST | `/v1/onsite/decide` | 온사이트 캠페인 노출 decision |
| POST | `/v1/onsite/impressions` | 온사이트 노출 수집 |
| POST | `/v1/onsite/clicks` | 온사이트 클릭 수집 |
| POST | `/v1/onsite/dismissals` | 온사이트 닫기 수집 |
| GET | `/v1/audiences/templates` | 기본 오디언스 템플릿 목록 |
| GET | `/v1/audiences/templates/{template_id}` | 기본 오디언스 템플릿 상세 |

## 6. 내부 구조

현재 구조를 유지하되 기능 단위로 package를 추가한다.

```text
app/
  core/
    config.py
    database.py
    cache.py
    tenant.py
    observability.py
  events/
    schemas.py
    registry.py
  ingestion/
    domain/
    application/
    adapters/
  analytics/
    domain/
    application/
    adapters/
  audience/
    domain/
    application/
    adapters/
  prediction/
    domain/
    application/
    adapters/
  recommendation/
    domain/
    application/
    adapters/
  onsite/
    domain/
    application/
    adapters/
  ai/
    domain/
    application/
    adapters/
```

각 package는 `domain`, `application`, `adapters`를 가진다. HTTP router와 repository 구현은 adapter에 둔다.

## 7. 데이터 저장 및 연동

| 연동 | 개발 단계 | 운영 단계 |
|---|---|---|
| 이벤트 저장 | SQLite/PostgreSQL ORM | Kafka + Lake + OLAP |
| 지표 조회 | SQLAlchemy query | ClickHouse materialized view |
| 캐시 | optional Redis | Redis cluster |
| 모델 | versioned artifact 기반 ML scoring | model registry + feature store |
| 추천 | SQL 이벤트 feature + trained logistic ranking artifact | vector DB + feature store + ranking model |

## 8. 비기능 요구사항

- 수집 API는 provider 장애와 무관하게 빠르게 ack해야 한다.
- 분석 API는 cache hit 기준 p95 100ms 이하, OLAP 조회 기준 p95 800ms 이하를 목표로 한다.
- 모델 API는 online scoring p95 200ms 이하를 목표로 하고, 불가하면 batch score lookup을 사용한다.
- 모든 API는 tenant별 rate limit과 audit log를 적용한다.
- schema version을 이벤트와 응답 metadata에 포함한다.

## 9. 테스트 전략

- domain 단위 테스트: 외부 의존 없이 계산 규칙 검증.
- application 테스트: in-memory fake repository로 use case 검증.
- HTTP 테스트: FastAPI TestClient/httpx로 인증, validation, status 검증.
- adapter 통합 테스트: PostgreSQL, Redis, ClickHouse, Kafka는 testcontainers 또는 docker-compose로 검증.
- contract 테스트: 이벤트 schema backward compatibility 검증.

현재 저장소 기준 필수 검증 명령:

```bash
python3 -m pytest -q
python3 -m compileall app tests
```

## 10. 단계별 개발 계획

### Phase 1. 현재 lite 서비스 안정화

- 현재 수집/분석 API 테스트 유지.
- `app/events/schemas.py`를 Published Language로 정리.
- cache key와 tenant context 테스트 강화.
- OpenAPI 응답 schema 명확화.

### Phase 2. 운영형 수집 전환

- Kafka producer port와 adapter 추가.
- 저장 방식 설정: local DB mode / Kafka mode.
- event schema versioning 추가.
- 수집 실패 metric과 DLQ 정책 추가.

### Phase 3. OLAP 분석 전환

- AnalyticsRepository port를 유지하고 ClickHouse adapter 추가.
- materialized view 조회 모델 정의.
- Redis cache invalidation과 TTL 정책 정리.

### Phase 4. Prediction/Recommendation 추가

- purchase score, churn risk, product affinity API 추가.
- 추천 candidate/ranking use case 추가.
- 모델 버전과 feature version tracking 추가.

### Phase 5. AI Agent/Copy 추가

- 진단 prompt/tool contract 정의.
- Analytics/Audience/Campaign API tool adapter 구현.
- 생성 결과 review queue 연동을 Kotlin Campaign 서비스와 계약한다.

## 11. 완료 기준

- 모든 endpoint가 tenant 격리를 강제한다.
- domain/application 테스트가 외부 인프라 없이 통과한다.
- Kafka/ClickHouse/Redis adapter는 통합 테스트 또는 명시적 contract 테스트를 가진다.
- `python3 -m pytest -q`와 `python3 -m compileall app tests`가 성공한다.
- Python 서비스가 Kotlin 서비스와 공유하는 이벤트/API 계약 문서가 갱신된다.
