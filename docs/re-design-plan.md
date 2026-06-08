# Sunrise 운영 수준 전환 재설계 계획 (Re-Design Plan)

> 작성 기준: 2026-06-07 재감사 결과. 본 문서는 재감사에서 도출된 운영 미결 항목을
> **운영(production) 수준**으로 끌어올리기 위한 상세 실행 계획이다. 각 항목은
> `현황(증거) → 목표 → 변경 상세 → 테스트 → 의존성 → 리스크 → 완료기준(DoD)`
> 구조로 기술한다. 모든 변경은 `python3 -m pytest -q` 와 `python3 -m compileall app tests`
> 통과를 전제로 한다.

---

## 0. 원칙 / 스코프

- **Clean Architecture 유지**: `domain → application → adapters`. 포트(ABC) 계약을 깨지 않는 변경을 우선한다.
- **백엔드 대칭성**: 같은 유스케이스가 SQL/ClickHouse 양쪽에서 동일 의미로 동작해야 한다(현재 CH가 raw-scan으로 갈라진 것을 해소).
- **점진적·무중단**: 기존 엔드포인트 계약을 깨지 않고 deprecate → 대체 순으로 전환한다.
- **의존성 추가는 명시적 결정으로만** (아래 §12 의존성 정책 표). 기본은 표준 라이브러리, 운영 필수 영역(OTel, 확률 CLV)만 검증된 패키지 도입.
- **관측 가능성 우선**: 모든 silent fallback/절단은 metric/log로 노출한다.

### 우선순위 요약

| ID | 항목 | 등급 | 영향 | 의존성 |
|---|---|---|---|---|
| P0-1 | ClickHouse read model 정합화 (raw-scan 제거) | 🔴 P0 | 정합성 | 없음(클라 protocol 확장) |
| P0-2 | Prediction 학습→promote 파이프라인 + artifact 정합 | 🔴 P0 | 신뢰성 | 없음 |
| P1-3 | Audience profile feature 백필 | 🟡 P1 | 기능 완성 | 없음 |
| P1-4 | segments/refresh 캐시 무효화 | 🟡 P1 | 정합성 | 없음 |
| P1-5 | 레거시 `/revenue-breakdown` 정리 | 🟡 P1 | 혼선 제거 | 없음 |
| P2-6 | Core 관측성(OTel)·audit log·분산 rate limit | 🟠 P2 | 운영성 | opentelemetry, redis |
| P2-7 | Ingestion 폴백 buffer/outbox | 🟠 P2 | 신뢰성 | 없음 |
| P2-8 | Prediction 확률 CLV·drift 실측·explain·calibration | 🟠 P2 | ML 깊이 | (선택) lifetimes |
| P2-9 | AI LLM/agent tool·guardrail·review workflow | 🟠 P2 | 기능 | anthropic SDK |
| P2-10 | Onsite campaign 연동 seam | 🟠 P2 | 통합 | (Kotlin 계약) |

---

## P0-1. ClickHouse read model 정합화 (raw-scan 제거)

### 현황 (증거)
- `app/analytics/adapters/clickhouse.py`
  - `order_revenue_breakdown`(L570) → `_order_facts`(L509)가 `_events_relation` **raw-scan 후 Python fold**. `order_fact_v2` 테이블을 SELECT하지 않음.
  - `segment_snapshot`(L605) → `lifecycle_inputs` raw-scan 후 분류. `visitor_features_daily_v2` 미사용.
  - `cohort_retention`(L652) → `_cohort_event_times`(L614) raw-scan 후 `build_cohort_rows`. 코호트 CH 테이블 없음.
  - `refresh_*`(L554/596/637)는 **적재 없이 count만 반환** → refresh와 조회의 저장소가 불일치.
- init SQL(`clickhouse/init/001_events.sql`)에는 `order_fact_v2`(L105), `agg_metric_daily_v2`(L75), `visitor_features_daily_v2`(L138)가 존재하나 **어댑터가 읽지 않음**. cohort/segment-snapshot 전용 테이블은 없음.

### 목표
CH 모드에서도 "refresh가 read-model 테이블에 적재 → 조회는 그 테이블을 SELECT"하는 SQL 모드와 동일한 의미 체계를 갖춘다. raw-scan은 read-model 부재 시의 **명시적 폴백**으로만 남기고 metric으로 노출한다.

### 변경 상세

**(a) ClickHouse 클라이언트 포트 확장** — 현재 `ClickHouseClient` Protocol은 `query`만 가짐(L25). insert/command를 추가한다.
```python
class ClickHouseClient(Protocol):
    def query(self, sql, parameters=None): ...
    def command(self, sql, parameters=None): ...      # DDL/ALTER/DELETE
    def insert(self, table, rows, column_names): ...   # 행 적재
```
- 실 구현은 `clickhouse_connect` 클라이언트가 이미 `command`/`insert`를 제공 → `create_clickhouse_client`는 무변경.
- 테스트 `FakeClickHouseClient`에 `command`/`insert` 기록 메서드 추가.

**(b) order_revenue_breakdown → `order_fact_v2` 조회로 전환**
- `order_fact_v2`는 ReplacingMergeTree(order grain)로 이미 전역 dedup됨. 어댑터는 다음으로 교체:
```sql
SELECT sumIf(amount,1) total,
       sumIf(amount, onsite_matched) onsite
FROM (SELECT order_id, argMax(amount,received_at) amount,
             argMax(onsite_matched,received_at) onsite_matched
      FROM order_fact_v2
      WHERE tenant_id={t} AND occurred_at>={s} AND occurred_at<{e}
      GROUP BY order_id)
```
- `attributed_revenue`는 `order_fact_v2`에 attribution 컬럼이 없으므로 **MV 확장**(아래 c) 또는 touchpoint join으로 산출.
- `refresh_order_facts`(CH)는 MV가 자동 적재하므로 **count 반환 유지**하되, "MV 자동 적재"임을 응답 메타와 로그로 명시.

**(c) order_fact_v2 MV에 attribution 컬럼 추가** (정확한 기여 매출용)
- 대안 A(권장): `attribution_touchpoint` 테이블 + 서빙 시 join. 단일 MV로 attribution을 못 하는 한계(블록 단위) 때문.
- 대안 B(경량): `order_fact_v2`에 `attributed Bool`, `attributed_channel`을 두고, 별도 배치(Python `command` INSERT…SELECT)로 touchpoint join 결과를 채움.
- 1차는 total/onsite/hidden을 MV로 정확화하고, attributed는 touchpoint join 배치로 별도 채운다(점진).

**(d) cohort_retention → CH 전용 read 테이블 도입**
- 코호트는 "방문자 첫 활동 버킷"이 필요해 단순 MV로 어려움. **Python refresh가 CH 테이블에 insert**하는 방식으로 통일:
  - init SQL에 `cohort_retention_v1`(tenant, cohort_type, granularity, cohort, offset, base_count, retained_count, retention_rate, computed_at) ReplacingMergeTree(computed_at) 추가.
  - CH `refresh_cohort_retention`: `_cohort_event_times`로 산출 → `build_cohort_rows` → `client.insert("cohort_retention_v1", rows)` (delete-then-insert는 `ALTER TABLE … DELETE` 또는 ReplacingMergeTree+`computed_at` 최신본 채택).
  - CH `cohort_retention`: 위 테이블 SELECT(최신 computed_at).

**(e) segment_snapshot → `visitor_features_daily_v2` 집계 + 분류**
- as_of 시점까지의 `visitor_features_daily_v2`를 `argMax`/`sum`으로 집계해 `last_seen_at`/`last_purchase_at`/`purchase_count` 도출 후 `visit_segment`/`purchase_segment` 분류.
- 또는 cohort와 동일하게 `customer_segment_daily_v1` CH 테이블에 refresh가 insert.
- 일관성을 위해 **refresh-insert 방식(cohort와 동일)** 권장.

**(f) raw-scan 폴백 계측**
- read-model 테이블이 비었거나 미존재 시 raw-scan 폴백 + `observability.record("analytics_raw_scan_fallback", backend, feature)` 카운터 증가 → 운영에서 "read-model 미적재" 가시화.

### 테스트
- `FakeClickHouseClient`에 `insert`/`command` 기록 + `order_fact_v2`/`cohort_retention_v1` 행 반환 분기 추가.
- `test_clickhouse_analytics.py`: (1) refresh가 insert를 호출하는지, (2) breakdown/cohort 조회가 read-model 테이블 SELECT를 사용하는지(쿼리에 `FROM order_fact_v2`/`cohort_retention_v1` 포함) 단언.
- SQL 모드와 동일 입력→동일 출력 **백엔드 동치성 테스트** 추가(파라미터라이즈).

### 의존성 / 리스크
- 의존성 없음(`clickhouse_connect`는 이미 선택적). 리스크: 실 ClickHouse 통합 테스트는 testcontainers 필요 → 단위는 Fake로, 통합은 별도 CI 잡으로 분리.
- DDL 마이그레이션은 `clickhouse_migrations.py` 경로로 idempotent 적용.

### 완료기준(DoD)
- CH 모드 order/cohort/segment 조회 SQL에 read-model 테이블명이 실제 포함된다.
- CH refresh가 테이블에 적재(insert)하며, refresh→조회가 동일 저장소를 본다.
- raw-scan 폴백은 metric으로 노출되고 기본 경로가 아니다.
- SQL/CH 동치성 테스트 통과.

---

## P0-2. Prediction 학습→promote 파이프라인 + artifact 정합

### 현황 (증거)
- 서빙 `app/prediction/models/prediction_model.json`의 `training_data` 키 = `{source, purchase_positive_label, churn_positive_label, affinity_positive_label, sample_count:184230, positive_rate:0.213}`, 그리고 `drift_baseline` 존재.
- 학습 `train_model.py:234` `build_artifact`가 내보내는 `training_data` 키 = `{source, sample_count, purchase_positive_rate, churn_positive_rate, affinity_positive_rate}`, **`drift_baseline` 미출력**.
- → 서빙 artifact는 학습 산출물이 아님(hand-filled 확정). `sample_count:184230`/`metrics.purchase_auc:0.792`는 검증 불가 값.
- backtest: `_head_metrics`(train_model.py:153)가 **학습에 쓴 동일 rows로 AUC** 계산 → in-sample(holdout 없음).

### 목표
"학습 코드가 만든 artifact만 서빙된다"를 보장하고, backtest를 holdout 기반 out-of-sample 지표로 바꾼다.

### 변경 상세
**(a) artifact 스키마 단일화 + 검증 강화**
- `model_registry.py` 로더에 **엄격 검증** 추가: 필수 키 집합·`training_data` 하위 키·`feature contract`(heads의 feature 이름 == VISITOR/AFFINITY_FEATURE_NAMES)·`metrics` 키 존재. 불일치 시 로드 거부.
- `build_artifact`가 `drift_baseline`을 **실제로 산출**해 포함(학습 feature 분포의 mean/std/quantile). 그래야 서빙 artifact와 학습 출력 스키마가 일치.

**(b) holdout backtest**
- `train_model.py`에 시간 분할(temporal split) 또는 random holdout(기본 20%) 추가. `metrics`는 holdout 기준 AUC/log_loss로 기록하고, in-sample 지표는 `metrics_in_sample`로 분리 표기.
- split seed/비율을 artifact `training_data`에 기록(재현성).

**(c) promote 절차 + 재현성 가드**
- `train_model.py`에 `--out app/prediction/models/prediction_model.json` 산출 + `--check` 모드(기존 서빙 artifact가 현재 학습 데이터로 재현되는지 비교) 추가.
- 회귀 테스트 `test_prediction_training.py`: 고정 fixture로 학습→artifact 생성→로더 검증→스코어 라운드트립. **hand-edit 방지**: 서빙 artifact의 `training_data.sample_count`가 학습 fixture 행수와 정합하거나, 최소한 로더가 키 스키마 불일치를 거부함을 단언.

**(d) 실데이터 학습 경로 문서화**
- `feature store/read model(customer_feature_daily) → CSV/Parquet export → train_model → registry promote` 절차를 README/RUNBOOK에 기술. 현재 SQLite/ClickHouse feature repo를 학습 데이터 export 소스로 연결.

### 테스트
- 로더가 (i) 키 누락, (ii) feature contract 불일치, (iii) `drift_baseline` 부재 artifact를 거부하는지.
- 학습→artifact의 `training_data` 키가 서빙 스키마와 동일한지.
- holdout 지표가 `metrics`에 기록되는지.

### 의존성 / 리스크
- 의존성 없음(현 stdlib 학습 유지). 리스크: 기존 서빙 artifact를 재학습본으로 교체 시 점수 분포 변동 → shadow 비교(이전/신규 점수 상관) 후 promote.

### 완료기준(DoD)
- 서빙 artifact가 `build_artifact` 출력 스키마와 100% 일치(키·feature contract).
- backtest 지표가 holdout 기반.
- 로더가 hand-filled/스키마 불일치 artifact를 거부.

---

## P1-3. Audience profile feature 백필

### 현황 (증거)
- `preview._build_features`(app/audience/application/preview.py)가 제공하는 profile 필드 = 14종
  (`first_seen_days_ago, days_since_last_seen, days_since_last_purchase, purchase_count, total_revenue, avg_order_value, cart_amount, cart_age_hours, view_count, cart_add_count, utm_medium, utm_source, top_category, onsite_frequency_available`).
- 템플릿이 참조하나 **미지원**인 필드(약 22종):
  - **이벤트로 도출 가능**: `category_purchase_count, full_price_purchase_count, high_margin_purchase_count, review_written, recent_campaign_exposure_days, expected_repurchase_days, engagement_score, cross_sell_score`
  - **상품 feature join 필요**: `discount_affinity, premium_affinity, free_shipping_affinity, free_shipping_gap, coupon_affinity, coupon_purchase_count, coupon_usage_rate, return_rate`
  - **외부 프로필/동의 데이터 필요**: `lifetime_value, kakao_opt_in, email_opt_in, sms_opt_in, email_verified, phone_verified`
- 미지원 조건은 `_evaluate_condition`에서 `unsupported`에 기록되고 **항상 False** → 해당 템플릿은 빈 모수.

### 목표
미지원 필드를 (1) 이벤트 도출, (2) 상품 feature join, (3) 외부 프로필 소스 3계층으로 분류해 백필하고, 미지원 필드는 "조용한 0 모수"가 아니라 **명시적 미지원 신호**로 응답한다.

### 변경 상세
**(a) 이벤트 도출 필드(즉시 가능)** — `_build_features` 확장. preview 스캔 시 product_id/category/utm_campaign/amount/original_price 등을 이미 읽으므로 다음을 계산:
- `category_purchase_count` = 방문자의 카테고리별 구매수(상위 카테고리 기준 또는 조건에 category 인자 추가).
- `full_price_purchase_count` / `high_margin_purchase_count` = 상품 feature join 필요(아래 b)와 결합.
- `review_written` = `review`/`review_written` 이벤트 카운트(이벤트 타입 확장 필요 시 §주석).
- `recent_campaign_exposure_days` = 최근 `campaign_impression` 이후 경과일.
- `expected_repurchase_days` = 구매 간격 중앙값(구매 2회 이상 시), 그 외 None.
- `engagement_score` = 정규화 가중합(view/cart/purchase/recency) — 도메인 순수 함수로 정의.
- `cross_sell_score` = 서로 다른 카테고리 구매 다양성.

**(b) 상품 feature join 필드** — `ProductFeatureRow`(price/original_price/gross_margin/return_rate)와 방문자 구매 상품을 join:
- `discount_affinity` = 할인 상품 구매 비율, `premium_affinity` = 카테고리 평균가 초과 구매 비율, `high_margin_purchase_count`, `return_rate`(구매 상품 가중 평균 반품률), `free_shipping_*`/`coupon_*`는 해당 신호가 이벤트/상품 메타에 없으면 (c)로 분류.
- preview 스캔에 product_features 조회를 추가(테넌트 단위 1회 로드 후 메모리 join).

**(c) 외부 프로필/동의 소스** — `lifetime_value`(예측 CLV read model), `*_opt_in`/`*_verified`(consent store)는 Sunrise Python 범위 밖 데이터. **`customer_profile_current`/`customer_consent_current` read model 포트**를 신설하고, 어댑터 미연동 시 해당 필드는 `unsupported`로 명시(현 동작 유지하되 카탈로그 메타에 "requires_profile_source" 태그).

**(d) 미지원 신호 표면화**
- 템플릿 카탈로그 응답에 각 템플릿의 `required_features`와 `unsupported_in_current_deployment`를 노출 → 콘솔이 "이 환경에서 평가 불가" 템플릿을 구분.
- preview 응답의 `unsupported_conditions`는 이미 존재 → 콘솔에서 경고로 사용.

**(e) score 신호 차별화** — `category_affinity`/`product_affinity`가 둘 다 `view_count/5` 휴리스틱인 문제: product-affinity 예측 head(이미 존재)를 키별로 호출해 실제 차별화. `next_best_offer`도 파생식 대신 별도 정의 또는 제거.

### 테스트
- 신규 이벤트 도출 필드 각각 단위 테스트(도메인 순수 함수).
- 상품 join 필드: product_features seed 후 preview 모수 검증.
- 미지원 필드 참조 템플릿이 카탈로그 메타에 `unsupported`로 표기되는지.

### 의존성 / 리스크
- (a)(b)는 의존성 없음. (c)는 외부 read model 필요 → 포트만 신설하고 어댑터는 후속.
- 리스크: 이벤트 타입에 `review` 등이 없으면 일부 필드는 영구 미지원 → 이벤트 계약 확장 별도 검토.

### 완료기준(DoD)
- 이벤트 도출 8필드 + 상품 join 6필드 백필 완료, 관련 템플릿이 실모수 반환.
- 외부 소스 필드는 명시적 미지원 메타로 노출(빈 모수 silent 금지).

---

## P1-4. segments/refresh 캐시 무효화

### 현황 (증거)
- `app/analytics/adapters/http.py`의 order-fact refresh(L686), cohort refresh(L529)는 `cache.delete(...)` 호출. **segments/refresh(L811 부근)는 무효화 없음.**

### 목표
segment 관련 캐시도 refresh 시 무효화해 다른 read model과 정합성을 맞춘다.

### 변경 상세
- `/segments/transitions`는 현재 캐시 미사용(스냅샷 조회 즉시 계산). 그러나 `/segments`(현재시점 lite)와 향후 snapshot 조회를 캐시한다면 키 패턴을 정해 refresh 시 삭제.
- 즉시 조치: segments/refresh에 `cache.delete(_cache_key(tenant, "segments", start, end))` 및 transitions 캐시 도입 시 해당 키 무효화 추가. 캐시 미적용 엔드포인트라면 **주석으로 "캐시 미사용" 명시**해 누락이 아님을 문서화.

### 테스트
- order-fact 무효화 테스트(`test_order_fact_refresh_invalidates_breakdown_cache`)와 동형으로 segment 무효화 테스트 추가(FakeCache 주입).

### 완료기준(DoD)
- segment refresh 후 동일 윈도우 조회가 stale을 반환하지 않음(테스트로 증명) 또는 "캐시 미사용" 명시.

---

## P1-5. 레거시 `/revenue-breakdown` 정리

### 현황 (증거)
- `/v1/analytics/revenue-breakdown`(repository.py:184)은 visitor-touch 근사(`campaign_impression/click` 방문자의 구매를 onsite로 추정) → **order dedup 안 됨**.
- `/v1/analytics/order-fact/revenue-breakdown`이 order_fact 기반 정확 경로. 두 엔드포인트 병존으로 혼선.

### 목표
정확 경로(order_fact)를 정식으로 삼고 레거시를 deprecate한다.

### 변경 상세
- 1차: 레거시 응답에 `deprecated: true` + `successor: "/v1/analytics/order-fact/revenue-breakdown"` 메타 추가, OpenAPI `deprecated=True` 표기.
- 2차: 레거시 내부 구현을 order_fact 경로로 위임(데이터 정합 통일). 단 order_fact 미적재 시 raw 근사 폴백 + metric.
- 3차(주기 후): 레거시 제거(메이저 버전).
- DataTalk(`GetDataTalk`)가 사용하는 revenue_breakdown도 order_fact 기반으로 교체해 리포트 정확화.

### 테스트
- 레거시/신규 동일 시나리오에서 order dedup 차이를 명시하는 테스트(레거시=근사, 신규=정확) 유지 후, 위임 전환 시 동치 테스트로 교체.

### 완료기준(DoD)
- OpenAPI에 레거시 deprecated 표기, DataTalk이 order_fact 기반 매출 사용.

---

## P2-6. Core 관측성(OTel) · audit log · 분산 rate limit

### 현황 (증거)
- `observability.py`: in-process 카운터 + `/ops/metrics` JSON. OTel/Prometheus exporter 없음. `requirements`에 opentelemetry 없음.
- audit log: `grep -rni audit app/` 0건.
- `rate_limit.py`: in-process 슬라이딩 윈도우(인스턴스-로컬). 멀티 인스턴스에서 한도 부정확.

### 목표
운영 추적(분산 trace)·감사(audit)·정확한 테넌트 rate limit을 갖춘다.

### 변경 상세
**(a) OpenTelemetry**
- `opentelemetry-sdk` + `opentelemetry-instrumentation-fastapi`(+ optional OTLP exporter) 추가.
- `main.py` lifespan에서 TracerProvider 설정, FastAPI 자동 계측, trace_id를 로그/응답 헤더에 전파.
- Kafka producer/consumer, ClickHouse query에 span 래핑.
- `/ops/metrics`는 Prometheus exposition 포맷(`prometheus_client` 또는 OTel metrics exporter)으로 전환.

**(b) Audit log**
- `audit_log` 테이블(마이그레이션) + `core/audit.py` 포트: `record(tenant, actor, action, resource, metadata, at)`.
- 쓰기/상태변경 엔드포인트(collect, materialize, snapshot, refresh, model promote, onsite decide)에 audit 기록 미들웨어 또는 의존성.

**(c) 분산 rate limit**
- `rate_limit.py`를 Redis 기반 토큰버킷/고정윈도우 카운터로 교체(`INCR`+`EXPIRE` 또는 Lua atomic). `cache.py`의 Redis 클라이언트 재사용.
- Redis 미설정(로컬/테스트) 시 현 in-process 폴백 유지(graceful degradation) + 운영 가드(`config.validate_production_settings`)에서 Redis rate limit 강제.

### 테스트
- audit: 주요 액션이 `audit_log`에 기록되는지.
- rate limit: Fake Redis(또는 in-memory atomic)로 한도 초과 429 검증, 멀티 인스턴스 시뮬레이션(공유 카운터).
- OTel: tracer 설정 시 span 생성/예외 기록 스모크.

### 의존성 / 리스크
- 신규: `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, (선택)`prometheus-client`, `redis`(기존). 리스크: 계측 오버헤드 → 샘플링 비율 설정.

### 완료기준(DoD)
- 요청에 trace_id 전파, `/ops/metrics` Prometheus 포맷.
- 주요 액션 audit 기록.
- rate limit이 Redis 공유 카운터로 멀티 인스턴스 정확.

---

## P2-7. Ingestion 폴백 buffer/outbox

### 현황 (증거)
- `kafka.py`: send 실패 시 retry → DLQ 발행. 단 **DLQ가 동일 producer** 사용 → Kafka 전면 장애 시 DLQ도 실패 → 최종 503, 이벤트 유실. 로컬 disk buffer/outbox 부재.

### 목표
Kafka 전면 장애에도 at-least-once를 보장한다(arch §9).

### 변경 상세
- **Outbox 패턴**: 발행 실패(+DLQ 실패) 이벤트를 `ingestion_outbox` 테이블(또는 append-only spool 파일)에 저장 후 즉시 ack. 백그라운드 relay 워커가 Kafka 복구 시 재발행.
- `config`로 `INGESTION_DURABILITY=kafka|outbox` 모드. outbox 모드는 SQL DB(이미 존재) 재사용.
- relay 워커: `app/ingestion/application/relay.py` + lifespan task 또는 별도 프로세스. 멱등(event_id) 보장으로 중복 재발행 안전.
- backpressure: outbox 적체 임계 초과 시 503 + metric.

### 테스트
- producer 전면 실패 주입 시 outbox 적재 + ack, 복구 후 relay 재발행 검증(Fake producer).

### 완료기준(DoD)
- Kafka/DLQ 동시 실패 시에도 이벤트가 outbox에 보존되고 복구 후 발행.

---

## P2-8. Prediction 확률 CLV · drift 실측 · explain · calibration

### 현황 (증거)
- CLV `_clv_from_features`(scoring.py:287): `survival=(0.15+0.35·log1p(freq)+0.50·exp(-recency/120))/1.55` 휴리스틱. BG/NBD·Pareto/NBD·Gamma-Gamma 없음.
- drift: 정적 baseline만 노출, 실측 계산 0건. explain: `value·weight` 선형(SHAP 아님). reason codes/calibration/`prediction_scores_v1` read model 부재.

### 목표
확률 기반 CLV, 실측 drift, calibration, 설명력을 갖춘다.

### 변경 상세
**(a) 확률 CLV**
- 도메인에 BG/NBD + Gamma-Gamma 구현. 옵션:
  - **옵션 A(권장, 운영)**: `lifetimes`(numpy/scipy/pandas) 도입 → 검증된 MLE. batch 학습 잡이 tenant별 calibration 파라미터 적합 후 artifact 저장, 서빙은 파라미터로 closed-form 기대값 계산.
  - **옵션 B(경량)**: stdlib로 BG/NBD 우도 + Nelder-Mead 자체 적합(정확도/안정성 책임 증가).
- 서빙 계약 유지(`survival_probability/expected_purchases/predicted_clv`), 내부만 교체. 적합 실패/콜드스타트는 현 휴리스틱 폴백.

**(b) drift 실측**
- `drift_baseline`(학습 분포) 대비 서빙 feature 분포의 **PSI/KL** 계산 잡 + `GET /model-status`에 `drift_status: ok|warn|drift` + 지표 노출.
- feature 분포 스냅샷을 `prediction_feature_stats_daily` read model에 적재.

**(c) calibration & reason codes**
- 점수 calibration(Platt/Isotonic) 버킷 → `calibration_bucket` 반환.
- churn reason codes: top contribution feature를 사람이 읽는 코드로 매핑.

**(d) read model**
- `prediction_scores_v1`(tenant, customer, as_of, model_family, version, survival, expected_purchases, predicted_clv, churn_risk, confidence, calibration_bucket, top_reasons) 배치 적재 → Audience/Campaign이 snapshot 점수 참조.

### 의존성 / 리스크
- 옵션 A는 numpy/scipy/pandas(무거움) → 학습 잡 환경에만 두고 서빙은 파라미터만 사용하도록 분리 권장. 리스크: 패키지 빌드/이미지 크기 → 학습/서빙 이미지 분리.

### 완료기준(DoD)
- CLV가 BG/NBD/Gamma-Gamma 기반(또는 명시적 폴백), drift가 실측 PSI, model-status에 calibration/drift 노출.

---

## P2-9. AI LLM/agent tool · guardrail · review workflow

### 현황 (증거)
- `agent.py`: 진단=임계값 규칙, suggestion=하드코딩 매핑+round-robin, copy=고정 f-string 3종. guardrail=금지어 substring 4종. review workflow 부재. `model.py` docstring이 "rule-based"로 자인.

### 목표
실제 LLM 생성 + 도구 사용 + 안전 가드레일 + 검토 워크플로를 갖춘다.

### 변경 상세
**(a) LLM 어댑터(ACL)**
- `app/ai/adapters/llm.py` 포트(`generate(prompt, tools) -> result`) + Anthropic SDK 어댑터(`claude` 모델). 키 미설정 시 현 규칙기반 폴백(graceful).
- diagnosis는 analytics tool(이미 함수 호출 중)을 **agent tool**로 노출해 모델이 필요한 지표를 조회하도록 전환(tool use 루프).
- copy 생성은 brand tone/상품 텍스트/목적을 프롬프트로 실제 생성, image_url은 멀티모달 입력.

**(b) Guardrail 실질화**
- 정책/유해성/브랜드 위반/사실성 점검을 LLM 기반 분류 + 규칙 병행. `guardrail_result`에 카테고리별 점수.

**(c) Review workflow**
- `ai_generation` + `ai_review_queue` 테이블, 상태기계(`pending→approved/rejected`), `requires_human_review` 시 큐 적재. Kotlin Campaign 서비스와 검토 계약(이벤트).

### 의존성 / 리스크
- `anthropic` SDK. 리스크: 비용/지연/비결정성 → 캐시·타임아웃·폴백·프롬프트 버전 관리. (LLM 사용은 사용자 승인 필요 — 도입 전 확인.)

### 완료기준(DoD)
- LLM 어댑터로 실제 생성(키 있을 때), 가드레일 카테고리 점수, 검토 큐 영속화.

---

## P2-10. Onsite campaign 연동 seam

### 현황 (증거)
- `decide.py`: `campaign_id=f"onsite-{trigger}-v1"`, priority 하드코딩. audience membership/experiment group 없음. frequency cap은 일반 EventRow 의존.
- 문서가 Kotlin campaign 연동을 향후 과제로 명시(arch §7.5/§11).

### 목표
활성 캠페인·대상자·실험군을 외부(Kotlin) 서비스와 연동할 seam을 만들고, 전용 cap 저장소를 둔다.

### 변경 상세
- `CampaignPort`(활성 캠페인 조회·우선순위·실험군 배정) 도메인 포트 신설. 기본 어댑터는 현 하드코딩 유지(폴백), 운영 어댑터는 Kotlin REST/이벤트 연동.
- `AudienceMembershipPort`로 decide 시 대상자 자격 검증(materialized audience read model 재사용 가능).
- frequency cap을 Redis 전용 카운터(`onsite_cap:{tenant}:{visitor}:{campaign}` + TTL 24h)로 이동(EventRow count 비용/정확도 개선). 폴백은 현 이벤트 쿼리.
- experiment group: holdout/variant 배정 결정 + decision 응답에 `experiment_group` 포함.

### 의존성 / 리스크
- Kotlin 서비스 계약 필요(외부). 리스크: 동기 호출 지연 → decision cache(Redis) + 타임아웃 폴백.

### 완료기준(DoD)
- CampaignPort/AudienceMembershipPort seam 존재(폴백 포함), cap이 Redis 전용 카운터.

---

## 12. 의존성 정책

| 패키지 | 용도 | 등급 | 비고 |
|---|---|---|---|
| (없음) | P0-1, P0-2, P1-3/4/5, P2-7 | — | 표준 라이브러리/기존 의존성만 |
| opentelemetry-sdk, -instrumentation-fastapi | P2-6 분산추적 | 운영 필수 | OTLP exporter 선택 |
| prometheus-client | P2-6 메트릭 노출 | 권장 | 또는 OTel metrics |
| redis (기존) | P2-6 rate limit, P2-10 cap | 기존 | 이미 사용 중 |
| lifetimes (numpy/scipy/pandas) | P2-8 확률 CLV | 선택(학습 잡 한정) | 서빙 이미지와 분리 |
| anthropic | P2-9 LLM 생성 | 선택 | **도입 전 사용자 승인** |

> 무거운 ML/LLM 의존성은 **학습/생성 잡 환경에만** 두고, 서빙 컨테이너는 파라미터·아티팩트만 로드해 경량 유지.

---

## 13. 테스트 전략

- **도메인 단위**: 모든 신규 순수 함수(engagement_score, BG/NBD 기대값, segment transition 등) 외부 의존 없이.
- **application**: in-memory/Fake 포트로 유스케이스.
- **adapter 동치성**: SQL ↔ ClickHouse 동일 입력→동일 출력 파라미터라이즈 테스트(P0-1 핵심).
- **계약**: 이벤트 schema·API 응답 backward compat.
- **통합(분리 CI)**: ClickHouse/Kafka/Redis는 testcontainers 잡으로 별도. 단위 CI는 Fake 유지.
- 회귀: prediction artifact 정합/로더 거부, 캐시 무효화, outbox 복구.

---

## 14. 롤아웃 순서 / 마이그레이션

1. **P0-1 → P0-2**(정합성/신뢰성 먼저). CH DDL 마이그레이션(cohort_retention_v1, customer_segment_daily_v1, order_fact_v2 attribution) idempotent 적용.
2. **P1-3/4/5**(기능 완성·혼선 제거). audience profile 백필은 이벤트 도출 → 상품 join → 외부 포트 순.
3. **P2-6/7**(운영성·신뢰성 횡단).
4. **P2-8/9/10**(ML 깊이·AI·통합) — 외부 의존/서비스 계약 필요 항목.
- SQL 마이그레이션 번호: 0009(audit_log), 0010(ingestion_outbox), 0011(prediction_scores_v1/feature_stats), 0012(ai_generation/review_queue) 등 순차.

---

## 15. 리스크 / 완화

| 리스크 | 완화 |
|---|---|
| CH 실인프라 없이 검증 한계 | Fake 단위 + testcontainers 통합 CI 분리, SQL/CH 동치성 테스트 |
| prediction artifact 교체로 점수 변동 | shadow 비교(상관/분포) 후 promote |
| LLM 비결정성·비용 | 폴백·캐시·타임아웃·프롬프트 버전, 도입 전 승인 |
| 무거운 의존성(이미지 크기) | 학습/서빙 이미지 분리 |
| 마이그레이션 다수 | idempotent·역마이그레이션·단계 배포 |

---

## 16. 완료 정의 (전체)

- analytics: SQL/CH 모두 read-model 테이블 조회(raw-scan은 계측된 폴백), 동치성 테스트 통과.
- prediction: 서빙 artifact == 학습 산출물, holdout backtest, drift 실측, (목표) 확률 CLV.
- audience: 이벤트/상품 도출 필드 백필, 미지원 필드 명시화.
- core: OTel trace·audit log·Redis rate limit.
- ingestion: outbox로 at-least-once 보장.
- ai/onsite: LLM·review·campaign seam(폴백 포함).
- 전 구간 `pytest`/`compileall` green 유지.
