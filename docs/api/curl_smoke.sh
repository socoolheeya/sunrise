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
  curl -sS --retry 5 --retry-all-errors --retry-delay 1 "$@"
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
      {"event_id": "curl-seed-001", "visitor_id": "v1", "type": "view", "product_id": "p-shirt", "category": "tops", "occurred_at": "2026-06-01T09:00:00Z"},
      {"event_id": "curl-seed-002", "visitor_id": "v1", "type": "cart_add", "product_id": "p-shirt", "category": "tops", "occurred_at": "2026-06-01T09:05:00Z"},
      {"event_id": "curl-seed-003", "visitor_id": "v1", "type": "purchase", "product_id": "p-shirt", "category": "tops", "amount": 120, "occurred_at": "2026-06-01T09:10:00Z"},
      {"event_id": "curl-seed-004", "visitor_id": "v2", "type": "view", "product_id": "p-bag", "category": "bags", "occurred_at": "2026-06-01T10:00:00Z"},
      {"event_id": "curl-seed-005", "visitor_id": "v2", "type": "cart_add", "product_id": "p-bag", "category": "bags", "occurred_at": "2026-06-01T10:05:00Z"},
      {"event_id": "curl-seed-006", "visitor_id": "v3", "type": "view", "product_id": "p-shoes", "category": "shoes", "occurred_at": "2026-06-02T11:00:00Z"},
      {"event_id": "curl-seed-007", "visitor_id": "v4", "type": "view", "product_id": "p-shirt", "category": "tops", "occurred_at": "2026-06-02T12:00:00Z"},
      {"event_id": "curl-seed-008", "visitor_id": "v4", "type": "purchase", "product_id": "p-shirt", "category": "tops", "amount": 80, "occurred_at": "2026-06-02T12:20:00Z"},
      {"event_id": "curl-seed-009", "visitor_id": "v4", "type": "purchase", "product_id": "p-bag", "category": "bags", "amount": 200, "occurred_at": "2026-06-03T12:20:00Z"}
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

request "Audience templates" \
  "${BASE_URL}/v1/audiences/templates" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience cart templates" \
  "${BASE_URL}/v1/audiences/templates?category=cart" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience purchase score templates" \
  "${BASE_URL}/v1/audiences/templates?query=구매가능성" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Audience template detail" \
  "${BASE_URL}/v1/audiences/templates/cart_added_no_purchase_24h" \
  -H "X-Sunrise-Key: ${API_KEY}"

request "Purchase score" \
  -X POST "${BASE_URL}/v1/predictions/purchase-score?start=${START}&end=${END}" \
  -H "Content-Type: application/json" \
  -H "X-Sunrise-Key: ${API_KEY}" \
  -d '{"visitor_ids":["v1","v2","v3","unknown"]}'

request "Churn risk" \
  -X POST "${BASE_URL}/v1/predictions/churn-risk?start=${START}&end=${END}" \
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
    "limit": 3
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
