#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-test-prod-key}"
START="${START:-2026-06-01T00:00:00Z}"
END="${END:-2026-06-04T00:00:00Z}"
INGESTION_WAIT_SECONDS="${INGESTION_WAIT_SECONDS:-3}"

request() {
  local title="$1"
  shift
  printf '\n### %s\n' "$title"
  curl -fsS --retry 5 --retry-all-errors --retry-delay 1 "$@"
  printf '\n'
}

request "Health" "${BASE_URL}/healthz"
request "Ready" "${BASE_URL}/readyz"

request "Collect seed events" \
  -X POST "${BASE_URL}/v1/collect" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{
    "schema_version": "tracking-event.v1",
    "events": [
      {"event_id": "curl-seed-001", "visitor_id": "v1", "session_id": "s-curl-1", "type": "view", "product_id": "p-shirt", "category": "tops", "utm_source": "kakao", "utm_medium": "paid_social", "utm_campaign": "summer_recovery", "landing_page": "https://shop.example/tops", "occurred_at": "2026-06-01T09:00:00Z"},
      {"event_id": "curl-seed-002", "visitor_id": "v1", "session_id": "s-curl-1", "type": "cart_add", "product_id": "p-shirt", "category": "tops", "utm_source": "kakao", "utm_medium": "paid_social", "utm_campaign": "summer_recovery", "occurred_at": "2026-06-01T09:05:00Z"},
      {"event_id": "curl-seed-003", "visitor_id": "v1", "session_id": "s-curl-1", "order_id": "o-curl-1", "type": "purchase", "product_id": "p-shirt", "category": "tops", "utm_source": "kakao", "utm_medium": "paid_social", "utm_campaign": "summer_recovery", "amount": 120, "occurred_at": "2026-06-01T09:10:00Z"},
      {"event_id": "curl-seed-004", "visitor_id": "v2", "session_id": "s-curl-2", "type": "view", "product_id": "p-bag", "category": "bags", "utm_source": "google", "utm_medium": "organic", "landing_page": "https://shop.example/bags", "occurred_at": "2026-06-01T10:00:00Z"},
      {"event_id": "curl-seed-005", "visitor_id": "v2", "session_id": "s-curl-2", "type": "cart_add", "product_id": "p-bag", "category": "bags", "utm_source": "google", "utm_medium": "organic", "occurred_at": "2026-06-01T10:05:00Z"},
      {"event_id": "curl-seed-006", "visitor_id": "v3", "session_id": "s-curl-3", "type": "view", "product_id": "p-shoes", "category": "shoes", "utm_source": "direct", "utm_medium": "direct", "occurred_at": "2026-06-02T11:00:00Z"},
      {"event_id": "curl-seed-007", "visitor_id": "v4", "session_id": "s-curl-4", "type": "view", "product_id": "p-shirt", "category": "tops", "utm_source": "email", "utm_medium": "email", "utm_campaign": "repeat_purchase", "occurred_at": "2026-06-02T12:00:00Z"},
      {"event_id": "curl-seed-008", "visitor_id": "v4", "session_id": "s-curl-4", "order_id": "o-curl-4a", "type": "purchase", "product_id": "p-shirt", "category": "tops", "utm_source": "email", "utm_medium": "email", "utm_campaign": "repeat_purchase", "amount": 80, "occurred_at": "2026-06-02T12:20:00Z"},
      {"event_id": "curl-seed-009", "visitor_id": "v4", "session_id": "s-curl-5", "order_id": "o-curl-4b", "type": "purchase", "product_id": "p-bag", "category": "bags", "utm_source": "email", "utm_medium": "email", "utm_campaign": "repeat_purchase", "amount": 200, "occurred_at": "2026-06-03T12:20:00Z"}
    ]
  }'

sleep "${INGESTION_WAIT_SECONDS}"

request "Analytics metrics" \
  "${BASE_URL}/v1/analytics/metrics?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics funnel" \
  "${BASE_URL}/v1/analytics/funnel?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics cohort" \
  "${BASE_URL}/v1/analytics/cohort?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics benchmark" \
  "${BASE_URL}/v1/analytics/benchmark?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics inflow" \
  "${BASE_URL}/v1/analytics/inflow?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics revenue breakdown" \
  "${BASE_URL}/v1/analytics/revenue-breakdown?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics lifecycle segments" \
  "${BASE_URL}/v1/analytics/segments?start=${START}&end=${END}&limit=20" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Analytics DataTalk report" \
  "${BASE_URL}/v1/analytics/datatalk?start=${START}&end=${END}" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience templates" \
  "${BASE_URL}/v1/audiences/templates" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience cart templates" \
  "${BASE_URL}/v1/audiences/templates?category=cart" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience purchase score templates" \
  "${BASE_URL}/v1/audiences/templates?query=%EA%B5%AC%EB%A7%A4%EA%B0%80%EB%8A%A5%EC%84%B1" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience template detail" \
  "${BASE_URL}/v1/audiences/templates/cart_added_no_purchase_24h" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience preview" \
  -X POST "${BASE_URL}/v1/audiences/preview?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"rule":{"all":[{"type":"event_count","event":"cart_add","window_days":7,"op":"gte","value":1}]},"sample_limit":10}'

request "Audience materialize" \
  -X POST "${BASE_URL}/v1/audiences/materialize?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"audience_id":"curl-cart-audience","rule":{"all":[{"type":"event_count","event":"cart_add","window_days":7,"op":"gte","value":1}]},"sample_limit":10}'

request "Prediction model status" \
  "${BASE_URL}/v1/predictions/model-status" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Purchase score" \
  -X POST "${BASE_URL}/v1/predictions/purchase-score?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_ids":["v1","v2","v3","unknown"]}'

request "Prediction explain" \
  -X POST "${BASE_URL}/v1/predictions/explain?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_id":"v1","target":"purchase_score"}'

request "Churn risk" \
  -X POST "${BASE_URL}/v1/predictions/churn-risk?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_ids":["v1","v2","v4","unknown"]}'

request "Predicted CLV" \
  -X POST "${BASE_URL}/v1/predictions/clv?start=${START}&end=${END}&horizon_days=180" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_ids":["v1","v2","v4","unknown"]}'

request "Product affinity" \
  -X POST "${BASE_URL}/v1/predictions/product-affinity?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_id":"v1","keys":["p-shirt","tops","p-bag"]}'

request "Upsert product value features" \
  -X POST "${BASE_URL}/v1/recommendations/products" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{
    "products": [
      {"product_id": "p-shirt", "category": "tops", "price": 80, "original_price": 120, "gross_margin": 0.38, "rating": 4.7, "review_count": 320, "return_rate": 0.03, "in_stock": true},
      {"product_id": "p-bag", "category": "bags", "price": 70, "original_price": 100, "gross_margin": 0.35, "rating": 4.6, "review_count": 250, "return_rate": 0.03, "in_stock": true},
      {"product_id": "p-shoes", "category": "shoes", "price": 125, "original_price": 130, "gross_margin": 0.15, "rating": 3.8, "review_count": 12, "return_rate": 0.2, "in_stock": false}
    ]
  }'

request "Recommendation items" \
  -X POST "${BASE_URL}/v1/recommendations/items?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_id":"v1","placement":"widget","limit":5,"exclude_purchased":true,"exclude_out_of_stock":true,"out_of_stock":["p-shoes"]}'

request "Onsite decision" \
  -X POST "${BASE_URL}/v1/onsite/decide?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{
    "visitor_id": "v1",
    "current_event": "exit_intent",
    "page_url": "https://shop.example/cart",
    "placement": "popup",
    "recent": {
      "viewed_product_ids": ["p-shirt"],
      "cart_product_ids": ["p-shirt"],
      "purchased_product_ids": []
    },
    "limit": 3,
    "frequency_cap_per_day": 3
  }'

request "Onsite impression" \
  -X POST "${BASE_URL}/v1/onsite/impressions" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"decision_id":"curl-decision-1","campaign_id":"onsite-cart_recovery-v1","visitor_id":"v1","product_id":"p-shirt","category":"tops"}'

request "Onsite click" \
  -X POST "${BASE_URL}/v1/onsite/clicks" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"decision_id":"curl-decision-1","campaign_id":"onsite-cart_recovery-v1","visitor_id":"v1","product_id":"p-shirt","category":"tops"}'

request "Onsite dismissal" \
  -X POST "${BASE_URL}/v1/onsite/dismissals" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"decision_id":"curl-decision-1","campaign_id":"onsite-cart_recovery-v1","visitor_id":"v1","product_id":"p-shirt","category":"tops"}'

request "AI site diagnosis" \
  -X POST "${BASE_URL}/v1/ai/diagnoses/site?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"focus":"conversion"}'

request "AI campaign suggestions" \
  -X POST "${BASE_URL}/v1/ai/suggestions/campaigns?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"preferred_channels":["kakao","onsite"],"max_suggestions":3}'

request "AI copy" \
  -X POST "${BASE_URL}/v1/ai/copy" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"brand_tone":"friendly","campaign_goal":"recover abandoned carts","product_name":"Linen Shirt","product_text":"Lightweight summer shirt","image_url":"https://example.com/shirt.jpg","count":2}'

request "AI copy guardrail example" \
  -X POST "${BASE_URL}/v1/ai/copy" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"brand_tone":"friendly","campaign_goal":"guaranteed risk-free sale","product_name":"Serum","count":1}'
