# Sunrise 시스템 분석 및 아키텍처

> `docs/system-analysis.md`의 공식 페이지 목록과 현재 저장소의 FastAPI 구현을 바탕으로 작성한 외부 추론 아키텍처입니다. 실제 Sunrise 내부 구현과 다를 수 있으며, 본 문서는 동일한 제품 문제를 해결하기 위한 설계 기준으로 사용합니다.

## 1. 분석 대상

`docs/system-analysis.md`는 다음 제품 페이지를 분석 대상으로 지정한다.

| 구분 | 페이지                                       | 시스템 관점의 의미 |
|---|-------------------------------------------|---|
| Why Sunrise | https://www.datarise.ai/why-sunrise       | 첫 방문부터 재구매까지 행동 데이터를 추적하고, 구매 가능성·구매주기 이탈률·상품 노출·퍼널 인사이트를 제공 |
| AI | https://www.datarise.ai/ai                | 하나의 데이터 기반으로 AI Agent, 고객 예측, 개인화, AI 카피, 상품 검색을 수행 |
| Audience | https://www.datarize.ai/audience          | 장바구니·관심상품·구매패턴 기반 자동 오디언스, 50+ 템플릿, 커스텀 조건 조합, 구매가능성 점수, 실시간 모수 확인 |
| Onsite | https://www.datarise.ai/onsite            | 방문자 행동 순간에 팝업/배너를 실시간 노출 |
| Message | https://www.datarise.ai/message-ko        | 카카오·이메일·문자 메시지를 자동 발송하고 MAB, iROAS 리포트 제공 |
| Metrics | https://www.datarise.ai/metrics           | 매출, 세션, CVR, AOV 등 핵심 지표 대시보드 |
| Analytics | https://www.datarise.ai/analytics         | 퍼널, 코호트, 세그먼트, 벤치마크 분석 |
| Product Widget | https://www.datarise.ai/product-widget-ko | 쇼핑몰 내 상품 추천 위젯과 추천 성과 측정 |

현재 저장소는 `app/ingestion`, `app/analytics`, `app/prediction`, `app/recommendation`, `app/onsite`, `app/ai`, `app/core`, `app/events`로 구성된 FastAPI 서비스다. 행동 이벤트 수집, 멱등 저장, 지표/퍼널/코호트/벤치마크 조회, ML artifact 기반 예측/추천, 온사이트 decision/tracking, AI 진단/제안/카피를 Clean Architecture 형태로 나누고 있다.

## 2. 제품 문제 정의

Sunrise류 플랫폼의 핵심 문제는 "이커머스 고객 행동을 전 구간에서 수집하고, 이를 실행 가능한 개인화 마케팅으로 자동 전환하는 것"이다.

핵심 루프는 다음과 같다.

```text
행동/상품/구매 데이터 수집
  -> 고객 식별 및 프로필 생성
  -> 지표·퍼널·코호트·벤치마크 분석
  -> 구매 가능성/이탈/상품 반응 예측
  -> 오디언스 조건 조합 및 실시간 모수 확인
  -> 오디언스·캠페인·콘텐츠 결정
  -> 메시지/온사이트/위젯 개인화 전달
  -> 노출·클릭·구매·iROAS 성과 환류
```

이 루프가 성립하려면 시스템은 다음 특성을 가져야 한다.

- 다수 쇼핑몰 테넌트의 행동 데이터를 안정적으로 수집한다.
- 수집 경로는 쓰기 폭주를 견디고, 분석 경로는 무거운 집계를 빠르게 응답한다.
- 캠페인·메시지·결제처럼 상태와 정합성이 중요한 영역은 트랜잭션 경계를 명확히 둔다.
- AI/분석 영역은 빠른 실험과 모델 서빙을 위해 Python 생태계를 활용한다.
- 모든 외부 채널, PG, 쇼핑몰 연동은 Anti-Corruption Layer로 격리한다.

## 3. 전체 아키텍처

```text
쇼핑몰 1-script / API 연동
        |
        v
Ingestion Edge (FastAPI)
  - event schema validation
  - tenant auth
  - idempotency
  - raw event append
        |
        v
Event Backbone (Kafka-compatible stream)
        |
        +--> Stream Processing / CDP
        |      - sessionize
        |      - identity resolution
        |      - profile/materialized features
        |
        +--> Lake / OLAP
        |      - raw events
        |      - aggregate tables
        |      - funnel/cohort/metric read models
        |
        +--> Trigger Events
               - cart abandonment
               - exit intent
               - purchase cycle churn

Read/AI Layer (Python/FastAPI)
  - metrics, analytics, benchmark
  - scoring, recommendation, AI copy, AI agent
  - 50+ audience template catalog
  - audience count estimation and ML-score features

Business Orchestration (Kotlin/Spring)
  - audience template, condition tree, materialization
  - campaign lifecycle
  - message job planning and dispatch
  - onsite/widget decision policy
  - billing/payment/settlement

Delivery Channels
  - Kakao, email, SMS/LMS/MMS, LINE
  - onsite popup/banner
  - product widget

Feedback
  - impression, click, open, conversion, purchase
  - attribution and iROAS
```

## 4. Bounded Context

| Context | 핵심 책임 | 권장 구현 |
|---|---|---|
| Tenant/IAM | 고객사, API key, 권한, 데이터 격리 | Kotlin/Spring 또는 공통 플랫폼 |
| Ingestion | 행동 이벤트 수집, 검증, 멱등 저장, 스트림 발행 | Python/FastAPI |
| Event Schema | 수집/성과/트리거 이벤트 계약 | Python package + schema registry |
| CDP/Profile | 익명/회원 식별 결합, 고객 프로필, 피처 materialization | Python + stream/batch |
| Metrics | 매출, 세션, CVR, AOV, 재구매율 지표 | Python/FastAPI |
| Analytics | 퍼널, 코호트, 벤치마크, 행동 분석 | Python/FastAPI |
| Prediction | 구매 가능성, 이탈 시점, 상품 반응 스코어 | Python/FastAPI |
| Recommendation | 상품 후보 생성, 랭킹, 위젯 추천 | Python/FastAPI |
| AI Agent/Copy | 사이트 진단, 캠페인 제안, 메시지 카피 생성 | Python/FastAPI |
| Audience | 50+ 기본 템플릿 catalog, 조건 트리, 구매가능성 점수 조건, 실시간 모수, 자동 갱신 대상자 산출 | Python/FastAPI catalog + Kotlin/Spring materialization |
| Campaign | 캠페인 상태, 스케줄, 트리거, 승인 | Kotlin/Spring |
| Message Delivery | 발송 job, 개인화 merge, 채널 발송, 영수증 처리 | Kotlin/Spring |
| Onsite Decision | 팝업/배너 노출 정책, 빈도 제어, 실험 | Kotlin/Spring + Python scoring |
| Widget Serving | 위젯 설정, 추천 API, 노출/클릭 수집 | Python/FastAPI 또는 Kotlin edge |
| Payment/Billing | 구독, 결제, 환불, 정산, 관리자 | Kotlin/Spring |

## 5. Clean Architecture 원칙

서비스 내부는 다음 의존성 규칙을 지킨다.

```text
domain <- application <- adapters <- infrastructure
```

- `domain`: Entity, Value Object, Aggregate, Domain Service. 외부 프레임워크 import 금지.
- `application`: Use Case. Repository/Publisher 같은 Port에만 의존.
- `adapters`: HTTP controller, SQL repository, Kafka producer, channel client 구현.
- `infrastructure`: FastAPI/Spring Boot 앱 조립, 설정, DB, cache, tracing.

현재 FastAPI 저장소의 `app/ingestion/domain`, `app/ingestion/application`, `app/ingestion/adapters`와 `app/analytics/...` 구조는 이 방향을 따른다. 향후 Kotlin 서비스도 같은 계층 규칙을 유지해야 Python/Kotlin 간 설계 일관성이 생긴다.

## 6. 데이터 모델과 이벤트 계약

### 6.1 핵심 이벤트

| 이벤트 | 예시 필드 | 용도 |
|---|---|---|
| `behavior.view` | tenant_id, visitor_id, item_id, category_id, url, occurred_at | 상품/페이지 조회, 퍼널 시작점 |
| `behavior.cart_added` | item_id, quantity, price | 장바구니 반응, 이탈 트리거 |
| `behavior.checkout_started` | order_attempt_id, amount | 주문서 진입 퍼널 |
| `behavior.purchase_completed` | order_id, amount, items | 매출, 재구매, 어트리뷰션 |
| `campaign.impression` | campaign_id, channel, variant_id | 노출 성과 |
| `campaign.click` | campaign_id, message_id, item_id | CTR, 추천 반응 |
| `campaign.dismiss` | campaign_id, decision_id, item_id | 온사이트 닫기/피로도 분석 |
| `audience.snapshot_created` | audience_id, rule_hash, count, generated_at | 캠페인 실행 시점 대상자 고정 |
| `audience.estimate_requested` | audience_id, rule_hash, estimated_count | 조건 편집 중 모수 산출 관측 |
| `message.delivered` | dispatch_id, provider, status | 발송 신뢰성 |
| `payment.settled` | subscription_id, amount, fee | 정산 및 관리자 |

### 6.2 멀티테넌시

- 모든 이벤트, aggregate, read model에는 `tenant_id`가 필수다.
- API에서 받은 tenant는 인증 컨텍스트에서만 주입한다. 클라이언트가 쿼리 파라미터로 tenant를 임의 지정하지 못하게 한다.
- OLAP/Lake는 `tenant_id` 기준 파티션 또는 primary key를 둔다.
- 캐시 키도 `tenant_id`, 기간, 필터, schema version을 포함한다.

### 6.3 Audience 페이지에서 도출한 요구사항

`https://www.datarize.ai/audience` 페이지는 오디언스를 "클릭 몇 번으로 장바구니, 관심상품, 구매패턴 기반의 지금 공략해야 할 고객을 자동 추출하는 기능"으로 설명한다. 시스템 관점에서는 다음 요구사항으로 해석한다.

| 페이지 신호 | 아키텍처 요구 |
|---|---|
| 50+ 기본 오디언스 템플릿 | `GET /v1/audiences/templates`가 versioned catalog로 제공한다. 예: 첫 방문자, 장바구니 이탈자, 재구매 고객 |
| 최대 2천만 개 조건 조합 | 조건 트리 DSL과 안전한 query planner가 필요하다. 단순 enum 필터가 아니라 방문 빈도, 구매 이력, 상품 관심도, ML score 조건을 조합해야 한다. |
| 구매가능성 점수 기반 오디언스 | Prediction context의 purchase score를 Audience 조건으로 사용할 수 있어야 한다. |
| 실시간 오디언스 모수 확인 | 조건 편집 중 예상 도달 수를 빠르게 계산하는 count/estimate API와 캐시가 필요하다. |
| 행동 변화에 따른 자동 갱신 | Audience membership은 정적 리스트가 아니라 이벤트/feature 갱신에 따라 재계산되는 materialized set이어야 한다. |
| 오디언스 화면 이미지 | 콘솔에는 기본 템플릿 목록, 커스텀 오디언스 생성, 구매가능성 점수 오디언스, 실시간 모수 확인 화면이 필요하다. |

이미지 alt 기준으로 확인된 화면은 다음 UI 구성 요소를 암시한다.

- 데이터라이즈 오디언스 화면: 오디언스 기능의 메인 콘솔.
- 오디언스 기본 템플릿 화면: 바로 사용할 수 있는 기본 세그먼트 라이브러리.
- 커스텀 오디언스 생성 화면: 조건 조합 빌더.
- 구매가능성 점수 오디언스 소개 화면: ML score 기반 target 조건.
- 실시간 오디언스 모수 확인 화면: 필터 변경 시 예상 도달 수 preview.

## 7. 주요 흐름

### 7.1 수집 흐름

1. 쇼핑몰 1-script가 `POST /v1/collect`로 이벤트 batch를 전송한다.
2. Collector가 API key로 tenant를 식별하고 Pydantic schema를 검증한다.
3. `event_id`로 멱등 처리한다.
4. Lite 버전은 DB에 저장하고, 운영 버전은 Kafka `raw.events`에 append한다.
5. Stream processor가 정제, 세션화, 식별 결합, OLAP 적재를 수행한다.

### 7.2 분석/지표 흐름

1. Console이 지표/분석 API를 호출한다.
2. API는 tenant context와 기간을 검증한다.
3. Redis cache hit이면 즉시 반환한다.
4. miss이면 OLAP 또는 사전집계 테이블에서 조회한다.
5. 도메인 모델로 계산 후 DTO로 응답한다.

### 7.3 오디언스 생성/모수 확인 흐름

1. 운영자가 기본 템플릿을 선택하거나 커스텀 오디언스 조건 트리를 만든다.
2. Python Audience API가 50+ 기본 템플릿 catalog와 조건 DSL rule을 제공한다.
3. Audience 서비스가 조건 DSL을 검증하고 `rule_hash`를 생성한다.
4. Count API가 ClickHouse feature/read model과 prediction score read model을 조회한다.
5. Redis가 동일 조건의 모수 결과를 짧은 TTL로 캐시한다.
6. Console은 필터 변경마다 예상 도달 수를 보여준다.
7. 캠페인 실행 시점에는 audience snapshot을 생성해 대상자 집합을 고정한다.
8. 이후 이벤트/feature 변경에 따라 동적 오디언스 membership은 자동 재계산된다.

### 7.4 캠페인/메시지 흐름

1. Audience가 세그먼트 조건 트리와 ML score 조건으로 대상자를 계산한다.
2. Campaign이 발송 목적, 채널, 소재, 스케줄, holdout 정책을 확정한다.
3. Message Planner가 대상자를 chunk로 나누고 outbox에 job을 기록한다.
4. Worker가 개인화 데이터를 merge하고 채널 rate limit에 맞춰 발송한다.
5. Delivery receipt와 성과 이벤트가 다시 Metrics/Analytics로 들어온다.

### 7.5 온사이트/위젯 흐름

1. 방문자가 페이지를 열면 1-script가 decision request를 보낸다.
2. Decision service가 visitor state, trigger, frequency cap, campaign priority를 평가한다.
3. 필요한 경우 Python scoring/recommendation API를 호출한다.
4. 선택된 popup/banner/widget payload를 반환한다.
5. impression/click/purchase가 이벤트로 환류된다.

## 8. 저장소 전략

| 저장소 | 역할 |
|---|---|
| PostgreSQL | 캠페인, 세그먼트 정의, 결제, 정산, 관리자, 테넌트 설정 |
| ClickHouse/Druid | 이벤트 분석, 퍼널, 코호트, 실시간 집계, 오디언스 모수 산출 |
| S3/Parquet Lake | 원천 이벤트, ML 학습 데이터, 장기 보관 |
| Redis | API cache, rate limit counter, decision cache, audience count preview cache |
| Kafka | 이벤트 백본, message job, trigger event, outbox relay |
| Vector DB | 상품 검색, 추천 candidate, semantic query |

Lite 버전은 SQLite로 시작하지만, 운영 요구사항은 위 저장소 분리를 전제로 한다.

## 9. 신뢰성 설계

- 수집: at-least-once 수집, `event_id` 멱등성, backpressure, validation failure metric.
- 메시지: outbox, dispatch idempotency key, provider별 circuit breaker, DLQ, retry with backoff.
- 캠페인: 상태 기계, 승인/중지/재개 audit log, holdout group 고정.
- 결제: PG webhook 멱등 처리, payment state transition, reconciliation batch.
- 분석: read model 재생성 가능성, schema versioning, cache invalidation 정책.

## 10. 관측성과 운영

- 기술 지표: request latency, error rate, Kafka lag, DB pool, cache hit ratio.
- 비즈니스 지표: 수집 TPS, 이벤트 누락률, 캠페인 발송 성공률, 오픈/클릭/전환, iROAS, 정산 불일치.
- 추적: OpenTelemetry trace id를 API, stream job, worker, provider call에 전파한다.
- 배포: Docker image, Kubernetes, GitHub Actions, ArgoCD GitOps.

## 11. 현재 저장소와 목표 아키텍처의 차이

| 항목 | 현재 저장소 | 목표 |
|---|---|---|
| 수집 저장 | SQLite/PostgreSQL ORM 저장 | Kafka append + OLAP/Lake 적재 |
| 분석 | DB 기반 lite 집계 | ClickHouse 사전집계 + Redis cache |
| AI | 사이트 진단, 캠페인 제안, 카피 생성 | LLM/agent tool 연동과 review workflow |
| Prediction | ML artifact 기반 purchase/churn/affinity scoring | 모델 registry, drift monitoring, batch feature store |
| Recommendation | ML artifact 기반 ranking, 상품 가치 feature upsert | feature store, A/B ranking, vector candidate 확장 |
| Onsite | decision API와 impression/click/dismiss 수집 | 활성 campaign 연동, frequency cap 저장소, SDK |
| Audience | 50+ 템플릿 catalog API | 조건 DSL 편집기, 실시간 모수, snapshot materialization |
| Campaign/Message | 없음 | Kotlin/Spring 서비스로 분리 |
| Payment | 없음 | Kotlin/Spring 결제/정산 컨텍스트 |
| 배포 | 단일 FastAPI 앱 | service별 컨테이너 + K8s |

## 12. 구현 분리 원칙

- Python은 데이터, 분석, ML, AI, 추천, 수집 edge처럼 빠른 실험과 고처리량 I/O가 중요한 영역을 맡는다.
- Kotlin/Spring은 캠페인, 메시지, 결제, 정산, 관리자처럼 트랜잭션, 상태 전이, 외부 시스템 계약이 중요한 영역을 맡는다.
- 두 언어 간 계약은 REST/gRPC보다 이벤트와 schema registry를 우선한다. 동기 API는 decision/scoring처럼 즉시 응답이 필요한 곳으로 제한한다.
