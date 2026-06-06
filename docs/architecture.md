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

추가 분석 대상은 다음 자료를 포함한다. 이 자료들은 제품 페이지에서 드러난 기능을 운영 수준의 모델링, 데이터 정합성, 분석 read model 요구로 확장하기 위한 근거로 사용한다.

| 구분 | 페이지 | 시스템 관점의 의미 |
|---|---|---|
| 확률 기반 CLV | https://www.datarize.ai/blog/probabilistic-clv-bgnbd-paretonbd | BG/NBD, Pareto/NBD, Gamma-Gamma 기반 생존 확률, 예상 구매 횟수, 예측 CLV 모델링 요구 |
| AI 이탈·CLV 예측 | https://www.datarize.ai/blog/ai-churn-clv-prediction | 구매 이력 외 클릭, 방문, 장바구니, 메시지 반응 같은 행동 선행 신호를 피처로 사용하는 이탈/CLV 예측 요구 |
| 고객 데이터 SSOT | https://www.datarize.ai/blog/customer-data-ssot-importance | RFM, CLV, AI 예측의 전제인 identity resolution, 단일 고객 프로필, 데이터 정합성 요구 |
| SEO/AEO/GEO/Agentic Commerce | https://www.datarize.ai/blog/ecommerce-seo-aeo-geo-agentic-commerce-guide | 상품/콘텐츠 데이터가 검색, AI 답변, 에이전트 구매 여정에 노출되도록 구조화되는 catalog/feed 요구 |
| 코호트 가이드 | https://datarize.gitbook.io/docs/analytics/analytics/cohort | 방문/구매/회원가입 기준 코호트, 일·주·월 retention, 시장/기간 비교 read model 요구 |
| 방문·구매 세그먼트 가이드 | https://datarize.gitbook.io/docs/analytics/analytics/visit-order-segment | 방문활성/방문위험/방문비활성, 구매활성/구매위험/미구매, 세그먼트 이동 분석 요구 |
| 숨은 매출 분석 가이드 | https://datarize.gitbook.io/docs/analytics/analytics/revenue-breakdown | 온사이트/총 매출 차이, 기여 매출, 매출 breakdown read model 요구 |
| 유입 가이드 | https://datarize.gitbook.io/docs/analytics/analytics/inflow | 유입 채널별 세션, 구매, 전환 분석과 attribution dimension 요구 |
| 데이터톡 가이드 | https://datarize.gitbook.io/docs/analytics/datatalk | 매일 발송되는 사이트 프로파일링 리포트, 방문·매출·퍼널·리텐션·시장 비교 지표 요구 |

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
        |      - lifecycle segment and CLV features
        |
        +--> Lake / OLAP
        |      - raw events
        |      - aggregate tables
        |      - funnel/cohort/segment/inflow/revenue read models
        |
        +--> Trigger Events
               - cart abandonment
               - exit intent
               - purchase cycle churn
               - lifecycle segment transition

Read/AI Layer (Python/FastAPI)
  - metrics, analytics, benchmark
  - cohort, segment, inflow, revenue breakdown
  - CLV/churn scoring, recommendation, AI copy, AI agent
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
  - DataTalk daily report snapshot
```

## 4. Bounded Context

| Context | 핵심 책임 | 권장 구현 |
|---|---|---|
| Tenant/IAM | 고객사, API key, 권한, 데이터 격리 | Kotlin/Spring 또는 공통 플랫폼 |
| Ingestion | 행동 이벤트 수집, 검증, 멱등 저장, 스트림 발행 | Python/FastAPI |
| Event Schema | 수집/성과/트리거 이벤트 계약 | Python package + schema registry |
| CDP/Profile | 익명/회원 식별 결합, 고객 프로필, 피처 materialization | Python + stream/batch |
| Product Catalog | 상품/콘텐츠/가격/재고 SSOT, structured feed | Kotlin/Spring + search/feed workers |
| Metrics | 매출, 세션, CVR, AOV, 재구매율 지표 | Python/FastAPI |
| Analytics | 퍼널, 코호트, 세그먼트, 유입, 숨은 매출, 벤치마크 | Python/FastAPI |
| Prediction | 구매 가능성, 생존 확률, 예측 CLV, 이탈 위험, 상품 반응 스코어 | Python/FastAPI |
| Recommendation | 상품 후보 생성, 랭킹, 위젯 추천 | Python/FastAPI |
| AI Agent/Copy | 사이트 진단, 캠페인 제안, 메시지 카피 생성 | Python/FastAPI |
| Audience | 50+ 기본 템플릿 catalog, 조건 트리, 구매가능성 점수 조건, 실시간 모수, 자동 갱신 대상자 산출 | Python/FastAPI catalog + Kotlin/Spring materialization |
| Campaign | 캠페인 상태, 스케줄, 트리거, 승인 | Kotlin/Spring |
| Message Delivery | 발송 job, 개인화 merge, 채널 발송, 영수증 처리 | Kotlin/Spring |
| Onsite Decision | 팝업/배너 노출 정책, 빈도 제어, 실험 | Kotlin/Spring + Python scoring |
| Widget Serving | 위젯 설정, 추천 API, 노출/클릭 수집 | Python/FastAPI 또는 Kotlin edge |
| DataTalk | 일일 사이트 프로파일링 리포트 snapshot, 시장 비교, 발송 | Python batch + Message Delivery |
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
| `identity.linked` | anonymous_id, customer_id, confidence, source | 익명/회원/채널 ID를 단일 고객 프로필로 결합 |
| `profile.feature_updated` | customer_id, feature_set, effective_at | 예측/오디언스용 고객 피처 갱신 |
| `product.catalog_updated` | product_id, title, category, price, stock, content_hash | 추천, 검색, AI 답변, 에이전트 구매용 상품 catalog 갱신 |
| `order.synced` | order_id, channel, amount, onsite_matched | 플랫폼 주문과 온사이트 구매 추적 결과 결합 |
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
| 실시간 오디언스 모수 확인 | `POST /v1/audiences/preview`가 조건 DSL을 평가해 matched_count와 sample visitor를 반환한다. |
| 행동 변화에 따른 자동 갱신 | `POST /v1/audiences/materialize`가 rule hash 기준 materialized audience read model을 저장한다. |
| 오디언스 화면 이미지 | 콘솔에는 기본 템플릿 목록, 커스텀 오디언스 생성, 구매가능성 점수 오디언스, 실시간 모수 확인 화면이 필요하다. |

이미지 alt 기준으로 확인된 화면은 다음 UI 구성 요소를 암시한다.

- 데이터라이즈 오디언스 화면: 오디언스 기능의 메인 콘솔.
- 오디언스 기본 템플릿 화면: 바로 사용할 수 있는 기본 세그먼트 라이브러리.
- 커스텀 오디언스 생성 화면: 조건 조합 빌더.
- 구매가능성 점수 오디언스 소개 화면: ML score 기반 target 조건.
- 실시간 오디언스 모수 확인 화면: 필터 변경 시 예상 도달 수 preview.

### 6.4 CLV와 이탈 예측 요구사항

확률 기반 CLV 자료는 RFM을 넘어 거래 빈도, 최근성, 관측 기간, 평균 구매액으로 미래 구매를 예측하는 모델 계층을 요구한다. AI 이탈·CLV 자료는 여기에 클릭, 방문, 장바구니, 메시지 반응 같은 선행 행동 신호를 추가해야 한다고 해석한다.

| 기능 | 입력 데이터 | 산출물 | 구현 요구 |
|---|---|---|---|
| RFM baseline | 마지막 구매일, 구매 횟수, 구매 금액 | R/F/M score, 고객 등급 | 초기 세그먼트와 모델 fallback |
| BG/NBD 또는 Pareto/NBD | frequency, recency, T | 생존 확률, 향후 N일 예상 구매 횟수 | Python batch training, tenant별 calibration, backtest |
| Gamma-Gamma | 평균 주문금액, 구매 횟수 | 예상 주문금액, 예측 CLV | 구매 빈도 모델과 결합, 이상치 winsorization |
| ML churn | 방문/구매/장바구니/메시지 반응 피처 | churn_risk, reason codes | gradient boosting baseline, SHAP/feature contribution 저장 |
| ML CLV | 거래 피처 + 행동 피처 + 캠페인 반응 | predicted_clv, confidence | horizon별 모델, calibration, drift monitoring |

운영 API는 다음 형태가 필요하다.

| API | 용도 |
|---|---|
| `POST /v1/predictions/clv` | 고객별 예측 CLV, 생존 확률, 예상 구매 횟수 반환 |
| `POST /v1/predictions/churn-risk` | 고객별 이탈 위험과 주요 근거 반환 |
| `GET /v1/predictions/model-status` | 모델 버전, 학습 기간, backtest 지표, drift 상태 반환 |
| `POST /v1/predictions/explain` | 특정 고객 score에 대한 feature contribution 반환 |

Prediction 저장소는 `prediction_scores_v1` read model을 둔다.

| 필드 | 설명 |
|---|---|
| `tenant_id`, `customer_id`, `as_of_date` | 점수 기준 키 |
| `model_family`, `model_version`, `horizon_days` | 모델 계열과 예측 기간 |
| `survival_probability`, `expected_purchases`, `predicted_clv`, `churn_risk` | CLV/이탈 핵심 산출물 |
| `confidence`, `calibration_bucket`, `top_reasons` | 운영 신뢰도와 설명 가능성 |

### 6.5 고객 데이터 SSOT와 Identity Resolution

SSOT 자료는 RFM, 확률 CLV, AI 예측의 전제가 단일 고객 프로필이라고 강조한다. 따라서 CDP/Profile context는 단순 visitor table이 아니라 identity graph와 profile store를 가져야 한다.

| 기능 | 상세 요구 |
|---|---|
| Identity graph | cookie id, member id, email hash, phone hash, channel id, order customer id를 edge로 저장 |
| 결정적 매칭 | 로그인, 주문, 본인 인증, 이메일/전화 hash 일치처럼 확실한 연결을 우선 |
| 확률적 매칭 | device, IP, user agent, 행동 패턴은 낮은 confidence edge로 분리 |
| Profile merge | 고객 프로필 병합 시 source priority, 최신성, null 처리 규칙을 명시 |
| Consent/PII boundary | 원본 PII는 vault 또는 암호화 저장소에 격리하고 분석 계층은 hash/token만 사용 |
| Data quality | 중복 고객률, orphan event 비율, identity merge 충돌률, 주문 누락률을 관측 |

핵심 read model은 다음으로 분리한다.

| 모델 | 역할 |
|---|---|
| `customer_identity_edges` | 식별자 간 연결과 confidence 저장 |
| `customer_profile_current` | 현재 단일 고객 프로필 |
| `customer_feature_daily` | 예측/오디언스/추천용 일별 feature snapshot |
| `customer_consent_current` | 채널별 수신 동의와 목적 제한 |

### 6.6 코호트와 방문·구매 세그먼트 상세 요구사항

코호트 가이드는 방문/구매/회원가입 기준, 일·주·월 retention, 시장 비교와 기간 비교를 요구한다. 방문·구매 세그먼트 가이드는 개인화된 방문/구매 주기 기반 위험 판정과 세그먼트 이동 분석을 요구한다.

| 기능 | 상세 요구 |
|---|---|
| 코호트 기준 | 전체 방문 고객, 전체 구매 회원, 신규 가입 회원, 첫 방문/재방문 고객, 첫 구매/재구매 회원 |
| 기간 단위 | 일간 0~11일, 주간 0~11주, 월간 0~11개월 retention matrix |
| 비교 기준 | 내 사이트 평균, 동종업계 중앙값, 전체 사이트 중앙값 |
| 방문 세그먼트 | 방문활성, 방문위험, 방문비활성 |
| 구매 세그먼트 | 구매활성, 구매위험, 미구매, 첫구매, 재구매 |
| 세그먼트 이동 | 이전 기간 대비 이동 경로, 이동 비율, 상위 3개 source segment |

필요 read model:

| 모델 | grain | 주요 필드 |
|---|---|---|
| `cohort_retention_daily` | tenant, cohort_type, cohort_date, offset | base_count, retained_count, retention_rate, benchmark_median |
| `customer_lifecycle_segment_daily` | tenant, customer, date | visit_segment, purchase_segment, previous_segment, churn_probability |
| `segment_transition_daily` | tenant, date, segment_type, from, to | customer_count, transition_rate |

### 6.7 숨은 매출과 어트리뷰션 요구사항

숨은 매출 분석은 플랫폼 주문 데이터와 온사이트 추적 주문의 차이를 명확히 분리해야 한다. 메시지/온사이트/위젯 성과와 연결하려면 주문 매칭과 기여 매출 read model이 필요하다.

| 기능 | 상세 요구 |
|---|---|
| 총 매출 | 플랫폼 연동, 업로드, 외부 채널 주문을 포함한 전체 주문 기준 |
| 온사이트 매출 | 스크립트가 구매 완료 페이지에서 수집했거나 네이버페이처럼 온사이트로 추정 가능한 주문 |
| 숨은 매출 | 총 매출과 온사이트 매출의 차이, tracking loss 원인 분류 |
| 기여 매출 | 캠페인 노출/클릭 후 attribution window 안에서 발생한 주문 |
| breakdown | 채널, 캠페인, 디바이스, 유입, 신규/재방문, 회원/비회원 기준 drilldown |

필요 이벤트와 모델:

| 모델 | 설명 |
|---|---|
| `order_fact` | 주문 원장. 주문 상태, 취소 포함 여부, 매출 기준 금액을 명시 |
| `onsite_order_match` | order_id와 session/visitor/event의 매칭 결과 |
| `attribution_touchpoint` | impression/click/open/session touchpoint 저장 |
| `revenue_breakdown_daily` | 총 매출, 온사이트 매출, 숨은 매출, 기여 매출 집계 |

### 6.8 유입 분석과 Attribution Dimension

유입 가이드는 채널별 세션, 구매, 전환 분석을 요구한다. 따라서 sessionization 단계에서 유입 차원을 표준화해야 한다.

| 차원 | 예시 |
|---|---|
| channel_group | direct, organic_search, paid_search, paid_social, referral, email, kakao, sms |
| source/medium/campaign | UTM source, medium, campaign |
| landing_page | 첫 페이지 URL, path, query 정규화 |
| device | desktop, mobile, tablet, app webview |
| new_returning | 신규 세션, 재방문 세션 |

필요 API:

| API | 용도 |
|---|---|
| `GET /v1/analytics/inflow` | 유입 채널별 세션, 구매, 매출, CVR, AOV |
| `GET /v1/analytics/attribution` | first/last/non-direct/campaign attribution 비교 |

### 6.9 데이터톡 리포트 요구사항

데이터톡은 매일 오전 발송되는 사이트 프로파일링 리포트다. 단순 dashboard API가 아니라 정해진 시각에 전일 지표를 freeze하고, 시장 비교 등급까지 포함한 report snapshot을 생성해야 한다.

| 영역 | 지표 |
|---|---|
| 방문 | 세션 수, 신규 세션 비율, 참여 세션 비율, 세션 체류시간 중앙값, 세션 페이지뷰 중앙값 |
| 매출 | 총 구매건수, 총 매출액, 온사이트 구매건수, 온사이트 매출액, 구매전환율, 평균 구매상품수, 주문단가 |
| 퍼널 | 방문-상품조회, 상품조회-구매시도, 구매시도-구매완료 |
| 리텐션 | 7일/30일/90일 내 재구매 비율 |
| 시장 비교 | 전체/동종업계 분위수, 신호등 등급 |

필요 구성:

| 구성 요소 | 역할 |
|---|---|
| `datatalk_report_daily` | tenant별 전일 report snapshot |
| Scheduler | 매일 tenant timezone 기준 리포트 생성 |
| Message adapter | 알림톡/이메일 발송 |
| Anomaly detector | 스크립트 제거, 세션 급감, 퍼널 급변 감지 |

### 6.10 SEO/AEO/GEO와 Agentic Commerce 데이터 요구사항

검색·AI 답변·에이전트 구매 여정에서는 상품 데이터가 사람이 보는 상세 페이지뿐 아니라 기계가 이해할 수 있는 구조화 feed로 제공되어야 한다.

| 기능 | 상세 요구 |
|---|---|
| Product catalog SSOT | 상품명, 브랜드, 카테고리, 옵션, 가격, 재고, 이미지, 리뷰, 배송/반품 정책 |
| Structured data | schema.org Product/Offer/Review, OpenGraph, canonical URL |
| Feed export | 검색/광고/AI agent 소비용 JSON feed, delta feed, freshness metadata |
| Content enrichment | 상품 설명 요약, FAQ, 비교 속성, 사용 상황, 금칙어/허위표현 검수 |
| Agent policy | 구매 가능 여부, 재고, 가격 유효 시각, 반품 조건, 성인/제한 상품 정책 |

Recommendation과 AI Agent는 같은 catalog/profile/read model을 사용해야 한다. 별도 크롤링 결과를 만들면 가격·재고·추천 근거가 불일치한다.

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
6. 코호트/세그먼트/유입/숨은 매출은 raw event scan이 아니라 목적별 read model을 우선 조회한다.
7. 시장 비교가 필요한 지표는 benchmark snapshot과 join해 percentile 또는 median 대비 차이를 반환한다.

### 7.2.1 CLV/이탈 모델 흐름

1. Batch feature job이 `customer_feature_daily`를 생성한다.
2. 확률 모델 job이 frequency, recency, T, monetary feature로 생존 확률과 예상 구매 횟수를 계산한다.
3. ML job이 행동 선행 신호를 포함해 churn risk와 predicted CLV를 계산한다.
4. Model registry가 모델 버전, 학습 기간, backtest 지표를 기록한다.
5. Serving API는 최신 score snapshot을 반환하고, 필요 시 feature contribution을 함께 제공한다.
6. Audience와 Campaign은 score를 조건으로 사용하되, campaign snapshot에는 당시 score version을 고정한다.

### 7.2.2 DataTalk 리포트 흐름

1. Scheduler가 tenant timezone 기준 전일 데이터를 닫는다.
2. Analytics job이 방문, 매출, 퍼널, 리텐션, 시장 비교 지표를 계산한다.
3. Anomaly detector가 세션 급감, 퍼널 급변, 스크립트 제거 의심 신호를 표시한다.
4. `datatalk_report_daily`에 리포트 snapshot을 저장한다.
5. Message Delivery가 알림톡/이메일로 발송하고 delivery receipt를 기록한다.

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
| ClickHouse/Druid | 이벤트 분석, 퍼널, 코호트, 세그먼트, 유입, 숨은 매출, 실시간 집계, 오디언스 모수 산출 |
| S3/Parquet Lake | 원천 이벤트, ML 학습 데이터, 장기 보관 |
| Redis | API cache, rate limit counter, decision cache, audience count preview cache |
| Kafka | 이벤트 백본, message job, trigger event, outbox relay |
| Vector DB | 상품 검색, 추천 candidate, semantic query |
| Feature Store | 고객/상품/캠페인 피처 snapshot, training-serving skew 방지 |
| Model Registry | CLV/churn/recommendation 모델 버전, backtest, calibration, drift 상태 |
| Search/Feed Index | 상품 structured data, agentic commerce feed, semantic retrieval |

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
| 코호트 | 첫 구매월 기준 lite retention | 방문/구매/회원가입 기준 일·주·월 retention + 시장 비교 |
| 세그먼트 | 방문활성/방문위험/방문비활성, 구매활성/구매위험/미구매 API | 세그먼트 이동 분석, 시장 비교 |
| 유입/어트리뷰션 | channel/source/medium/campaign 기반 세션·구매·매출 API | ClickHouse 사전집계 + attribution rule 확장 |
| 숨은 매출 | 총 매출, 온사이트 매출, 기여 매출, tracking loss breakdown API | 정산/주문 SSOT와 reconciliation |
| DataTalk | 일일 사이트 프로파일링 API | snapshot 저장, 시장 비교, 알림톡/이메일 발송 |
| AI | 사이트 진단, 캠페인 제안, 카피 생성 | LLM/agent tool 연동과 review workflow |
| Prediction | ML artifact 기반 purchase/churn/affinity scoring | BG/NBD/Pareto/NBD/Gamma-Gamma CLV, 행동 피처 기반 churn/CLV, 모델 registry, drift monitoring, batch feature store |
| Recommendation | ML artifact 기반 ranking, 상품 가치 feature upsert | feature store, A/B ranking, vector candidate 확장 |
| Product Catalog | 추천용 상품 가치 feature | 상품/콘텐츠/가격/재고 SSOT, schema.org/feed, agentic commerce 정책 |
| Onsite | decision API, impression/click/dismiss 수집, 이벤트 기반 frequency cap | 활성 campaign 연동, 전용 cap 저장소, SDK |
| Audience | 50+ 템플릿 catalog, preview, materialization API | 조건 DSL 편집기, scheduled refresh, 대용량 membership store |
| Campaign/Message | 없음 | Kotlin/Spring 서비스로 분리 |
| Payment | 없음 | Kotlin/Spring 결제/정산 컨텍스트 |
| 배포 | 단일 FastAPI 앱 | service별 컨테이너 + K8s |

## 12. 구현 분리 원칙

- Python은 데이터, 분석, ML, AI, 추천, 수집 edge처럼 빠른 실험과 고처리량 I/O가 중요한 영역을 맡는다.
- Kotlin/Spring은 캠페인, 메시지, 결제, 정산, 관리자처럼 트랜잭션, 상태 전이, 외부 시스템 계약이 중요한 영역을 맡는다.
- 두 언어 간 계약은 REST/gRPC보다 이벤트와 schema registry를 우선한다. 동기 API는 decision/scoring처럼 즉시 응답이 필요한 곳으로 제한한다.
