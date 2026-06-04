# Kotlin/Spring 개발 PRD

## 1. 목적

Kotlin/Spring 서비스는 Sunrise 플랫폼에서 상태, 트랜잭션, 외부 계약, 운영 안정성이 중요한 업무를 담당한다. Python 서비스가 데이터/AI 기능을 제공하면 Kotlin 서비스는 이를 캠페인 실행, 메시지 발송, 온사이트 결정, 결제/정산으로 연결한다.

## 2. 범위

### 2.1 포함

- Tenant/IAM 및 API key/사용자 권한
- Audience 세그먼트 정의와 대상자 materialization
- Campaign 생성, 승인, 스케줄, 중지, 재개
- Message job planning, dispatch orchestration, provider adapter
- Kakao, email, SMS/LMS/MMS, LINE 채널 연동
- Onsite popup/banner decision policy
- Widget configuration 관리
- Payment, subscription, invoice, refund, settlement
- Admin/backoffice workflow
- Outbox, idempotency, retry, DLQ, audit log

### 2.2 제외

- 행동 이벤트 raw 수집 API
- 퍼널/코호트/벤치마크 집계 계산
- ML 모델 학습/서빙
- AI 카피 생성의 모델 호출 본체
- 상품 추천 ranking model

위 제외 범위는 Python/FastAPI 서비스가 담당한다.

## 3. 사용자와 Use Case

| 사용자 | 요구 |
|---|---|
| 마케터 | 오디언스를 만들고 캠페인을 승인/실행/중지 |
| 운영자 | 발송 실패, 정산 불일치, provider 장애를 추적 |
| 결제 관리자 | 구독, 과금, 환불, 정산을 관리 |
| Python 서비스 | 캠페인 제안과 스코어 결과를 전달 |
| 외부 채널/PG | webhook, receipt, settlement file을 전달 |

## 4. 서비스 구성

```text
kotlin-services/
  tenant-service
  audience-service
  campaign-service
  message-service
  onsite-service
  billing-service
  admin-bff
```

초기에는 단일 Spring Boot modular monolith로 시작할 수 있다. 단, module boundary와 database table ownership은 위 서비스 기준으로 유지한다. 트래픽과 조직 규모가 커지면 service별 배포로 분리한다.

## 5. 도메인 요구사항

### 5.1 Tenant/IAM

- Tenant, User, Role, Permission, ApiKey를 관리한다.
- API key는 scope와 만료일을 가진다.
- 모든 command는 tenant context와 actor를 audit log에 남긴다.
- Python 서비스와 공유되는 tenant 식별자는 immutable해야 한다.

### 5.2 Audience

- 세그먼트 조건은 조건 트리로 저장한다.
- 조건 타입: 행동 이벤트, 구매 이력, 상품/카테고리 반응, 구매 가능성 score, 이탈 risk, 캠페인 수신 이력.
- Audience size preview는 Python/OLAP API 또는 cached materialized table을 조회한다.
- 확정 Audience는 campaign execution 시점에 snapshot을 생성한다.
- 조건 schema는 versioned JSON으로 관리한다.

### 5.3 Campaign

- Campaign aggregate는 `DRAFT`, `READY`, `SCHEDULED`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELED` 상태를 가진다.
- 승인 전에는 발송 job을 만들 수 없다.
- campaign은 audience snapshot, channel strategy, content variants, schedule, holdout policy를 가진다.
- AI Agent 제안은 바로 실행하지 않고 draft campaign으로 저장한다.
- 모든 상태 전이는 command handler에서 검증하고 event를 발행한다.

### 5.4 Message Delivery

- Campaign 실행 시 MessageJob을 tenant/channel/provider/rate limit 기준으로 chunking한다.
- 각 Dispatch는 `(campaignId, customerId, channel, variantId)` idempotency key를 가진다.
- Kakao, email, SMS/LMS/MMS, LINE provider는 `MessageProvider` port 뒤에 둔다.
- provider별 rate limit, retry policy, circuit breaker를 설정한다.
- delivery receipt webhook은 중복 수신 가능하므로 멱등 처리한다.
- MAB는 variant별 성과를 받아 발송 비율을 조정한다.
- iROAS 측정을 위해 holdout group을 고정하고 campaign 성과에 포함한다.

### 5.5 Onsite

- Placement, Trigger, FrequencyCap, PriorityRule을 관리한다.
- Decision request는 visitor context, page context, tenant context를 포함한다.
- Decision policy는 다음을 평가한다: 활성 campaign, audience membership, trigger, frequency cap, priority, experiment group.
- 구매 가능성/추천이 필요하면 Python scoring/recommendation API를 호출한다.
- impression/click은 이벤트로 발행한다.

### 5.6 Widget Configuration

- 위젯 배치, 디자인, 노출 조건, 추천 strategy를 관리한다.
- Cafe24/Imweb/Makeshop/자체몰별 설치 설정을 저장한다.
- 추천 결과 본체는 Python Recommendation API를 호출하되, 노출 정책과 설정은 Kotlin이 소유한다.

### 5.7 Billing/Payment/Settlement

- Subscription plan, usage, invoice, payment, refund, settlement를 관리한다.
- PG 연동은 `PaymentGateway` port와 adapter로 분리한다.
- webhook은 idempotency key로 중복 처리한다.
- 결제 상태 전이는 명시적 state machine으로 관리한다.
- 정산 batch는 거래, 환불, 수수료, 세금, 미수금을 계산한다.
- 관리자 보정은 audit log와 reason code가 필수다.

## 6. API 초안

### 6.1 Audience/Campaign

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/audiences` | 오디언스 생성 |
| GET | `/api/audiences/{id}/preview` | 대상 규모 미리보기 |
| POST | `/api/campaigns` | 캠페인 draft 생성 |
| POST | `/api/campaigns/{id}/approve` | 승인 |
| POST | `/api/campaigns/{id}/schedule` | 예약 |
| POST | `/api/campaigns/{id}/pause` | 중지 |
| POST | `/api/campaigns/{id}/resume` | 재개 |
| GET | `/api/campaigns/{id}/performance` | 성과 조회 |

### 6.2 Message/Onsite

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/message/jobs/{id}/dispatch` | 발송 job 실행 |
| POST | `/api/webhooks/messages/{provider}` | 발송 결과 webhook |
| POST | `/api/onsite/decide` | 팝업/배너 decision |
| POST | `/api/onsite/impressions` | 노출 수집 |
| POST | `/api/widgets/configs` | 위젯 설정 |

### 6.3 Billing

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/subscriptions` | 구독 생성 |
| POST | `/api/payments/authorize` | 결제 승인 |
| POST | `/api/payments/{id}/refund` | 환불 |
| POST | `/api/webhooks/payments/{pg}` | PG webhook |
| GET | `/api/settlements` | 정산 목록 |
| POST | `/api/settlements/{id}/adjustments` | 관리자 보정 |

## 7. 내부 구조

각 module은 같은 계층을 가진다.

```text
campaign/
  domain/
    Campaign.kt
    CampaignStatus.kt
    CampaignPolicy.kt
    CampaignEvent.kt
  application/
    CreateCampaignUseCase.kt
    ApproveCampaignUseCase.kt
    ScheduleCampaignUseCase.kt
  adapter/
    inbound/web/
    outbound/persistence/
    outbound/event/
  infrastructure/
    CampaignConfiguration.kt
```

규칙:

- domain은 Spring annotation을 사용하지 않는다.
- application은 interface port에 의존한다.
- adapter가 JPA, Kafka, WebClient, provider SDK를 감싼다.
- transaction boundary는 application service에 둔다.
- domain event는 outbox로 저장한 뒤 relay한다.

## 8. 저장소와 메시징

| 요소 | 선택 |
|---|---|
| DB | PostgreSQL |
| ORM | Spring Data JPA 또는 jOOQ |
| Message bus | Kafka |
| Outbox | PostgreSQL outbox table + relay |
| Cache/Lock | Redis |
| HTTP client | Spring WebClient |
| Resilience | Resilience4j |
| Migration | Flyway |

## 9. Python 서비스 연동

| 필요 데이터 | 호출 방식 |
|---|---|
| Audience preview | Python Analytics/OLAP API 또는 Kafka materialized result |
| Purchase/churn/product score | Python Prediction API |
| Product recommendation | Python Recommendation API |
| AI campaign suggestion | Python AI Agent API가 draft 생성 command 호출 |
| Message copy | Python AI Copy API |
| Campaign performance | Kotlin event -> Python Metrics/Analytics 환류 |

동기 호출은 timeout, retry, circuit breaker를 반드시 설정한다. 캠페인 실행 본류는 Python 장애로 멈추지 않도록 batch score snapshot 또는 fallback policy를 가진다.

## 10. 비기능 요구사항

- 메시지 발송은 at-least-once job 처리와 dispatch 멱등성을 보장한다.
- provider 장애는 channel별 circuit breaker로 격리한다.
- 대형 tenant가 전체 worker를 점유하지 않도록 fair scheduling을 적용한다.
- 결제/정산 command는 강한 transaction boundary를 가진다.
- 모든 상태 변경은 audit log와 domain event를 남긴다.
- 개인정보와 결제 정보는 암호화/마스킹하고 최소 권한으로 접근한다.

## 11. 테스트 전략

- domain unit test: 상태 전이, 정책, 금액 계산.
- application test: fake port로 command/use case 검증.
- persistence test: Testcontainers PostgreSQL로 repository 검증.
- provider contract test: Kakao/email/SMS/PG adapter request/response 계약 검증.
- messaging test: outbox relay, idempotent consumer, DLQ.
- E2E: audience -> campaign -> message job -> receipt -> performance.

필수 검증 명령 예시:

```bash
./gradlew test
./gradlew build
```

현재 저장소에는 Kotlin 프로젝트가 없으므로 위 명령은 Kotlin 모듈 추가 후 적용한다. 현재 단계에서는 Python 저장소 검증 명령으로 문서 변경 영향을 확인한다.

## 12. 단계별 개발 계획

### Phase 1. Modular Monolith 기반 구축

- Gradle multi-module 또는 package-based module 생성.
- Tenant/IAM, Campaign, Audience, Message, Billing module 경계 정의.
- PostgreSQL/Flyway/Outbox 공통 인프라 구성.

### Phase 2. Audience/Campaign MVP

- 조건 트리 저장과 preview API 연동.
- Campaign draft/approve/schedule 상태 전이.
- AI Agent draft campaign 수신 command.

### Phase 3. Message Pipeline

- MessageJob chunking.
- Provider port/adapters.
- Dispatch idempotency, retry, DLQ.
- Delivery receipt webhook.

### Phase 4. Onsite/Widget Policy

- Placement/trigger/frequency cap.
- Decision API.
- Python scoring/recommendation 연동.
- impression/click event 발행.

### Phase 5. Billing/Settlement

- Subscription/payment/refund aggregate.
- PG adapter와 webhook.
- Invoice/settlement batch.
- Admin adjustment workflow.

## 13. 완료 기준

- domain이 Spring과 외부 SDK에 의존하지 않는다.
- campaign/message/payment 핵심 상태 전이가 테스트로 고정된다.
- outbox와 멱등 consumer가 통합 테스트를 가진다.
- provider adapter는 contract test를 가진다.
- Python 서비스와 공유하는 이벤트/API 계약이 문서화된다.
- Kotlin 모듈이 생긴 뒤 `./gradlew build`가 성공한다.
