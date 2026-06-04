---
title: Onsite campaign personalization needs a decision and tracking surface
date: 2026-06-04
category: docs/solutions/architecture-patterns
module: Python FastAPI onsite
problem_type: architecture_pattern
component: service_object
severity: high
applies_when:
  - Personalized recommendations exist but the product still needs real-time onsite campaign execution.
  - AI campaign suggestions describe audiences and triggers but do not actually decide whether to show a popup or banner.
  - Browser SDKs need a small API surface for decision, impression, click, and dismissal tracking.
tags: [onsite, campaign-decision, personalization, tracking, recommendation]
---

# Onsite campaign personalization needs a decision and tracking surface

## Context

The system already had behavior collection, analytics, prediction, recommendation, AI campaign suggestions, and copy generation. That was not enough to satisfy "show a personalized onsite message when the customer is browsing, hesitating, or about to leave." Those existing APIs explain or recommend; they do not decide and execute at the page moment.

## Guidance

Keep onsite execution as its own application surface:

- `POST /v1/onsite/decide` evaluates the current browser moment and returns whether a popup/banner/widget should render.
- `POST /v1/onsite/impressions`, `/clicks`, and `/dismissals` collect campaign interaction events.
- The decision response should include `decision_id`, `campaign_id`, `trigger`, `placement`, `creative`, recommendation items, and `frequency_cap_key`.
- The tracking endpoints should write normal tracking events so ClickHouse analytics can aggregate campaign performance through the same ingestion pipeline.

Recommendation and AI suggestions should feed the decision, not replace it. The decision layer owns timing and serving constraints such as current event, recent cart state, exit intent, placement, and frequency cap identity.

## Why This Matters

Without a decision API, a frontend SDK has to hardcode campaign timing rules or stitch together analytics, recommendations, and copy generation on its own. That creates inconsistent timing, poor attribution, and no single place to enforce future rules like active campaign status, audience membership, priority, experiments, and frequency caps.

## When to Apply

- Adding onsite popup/banner personalization.
- Turning campaign suggestions into executable user-facing experiences.
- Connecting ML recommendations to real-time conversion surfaces.
- Building SDK-facing APIs where every rendered message needs an auditable decision and interaction trail.

## Examples

The shipped minimal surface:

```text
POST /v1/onsite/decide
POST /v1/onsite/impressions
POST /v1/onsite/clicks
POST /v1/onsite/dismissals
```

The first implementation supports these trigger families:

- `cart_recovery`: exit/page-hide while cart products exist and no purchase is known.
- `exit_intent`: exit behavior without a cart recovery context.
- `browse_assist`: product/category browsing or idle exploration.

Tracking endpoints convert onsite interactions into these event types:

- `campaign_impression`
- `campaign_click`
- `campaign_dismiss`

Verification commands used:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m compileall app tests
venv/bin/python -m json.tool docs/api/sunrise.postman_collection.json
bash -n docs/api/curl_smoke.sh
docker compose build app
docker compose up -d app
docs/api/curl_smoke.sh
```

## Related

- `app/onsite/adapters/http.py`
- `app/onsite/application/decide.py`
- `app/onsite/domain/model.py`
- `app/events/schemas.py`
- `tests/test_onsite_api.py`
- `docs/api/curl_smoke.sh`
- `docs/api/sunrise.postman_collection.json`
- `docs/prd_python.md`
