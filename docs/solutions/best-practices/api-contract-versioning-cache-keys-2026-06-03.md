---
title: API contract versions must participate in validation and cache keys
date: 2026-06-03
category: docs/solutions/best-practices
module: Python FastAPI API contracts
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - Adding or changing versioned request or response schemas
  - Caching serialized API responses
  - Maintaining compatibility between Python services and downstream Kotlin services
tags: [api-contracts, schema-versioning, cache-keys, fastapi]
---

# API contract versions must participate in validation and cache keys

## Context

The Python PRD calls for event schema versioning and stable analytics cache keys. During the first implementation pass, the service added explicit `schema_version` values to collection and analytics responses, accepted a versioned collection request, and included the analytics response contract version in cache keys.

## Guidance

Treat API contract versions as active behavior, not only response metadata.

- Validate incoming request contract versions when clients provide them.
- Keep a default version for backward compatibility when existing clients omit it.
- Include response contract versions in cache keys for serialized API responses.
- Cover the version fields in OpenAPI schema tests so future changes remain visible.

## Why This Matters

If a response shape changes but cache keys do not include the contract version, clients can receive stale JSON serialized under an older shape. If requests accept arbitrary version strings, downstream services may assume compatibility that the service never guaranteed.

## When to Apply

- Adding `schema_version` fields to Pydantic DTOs.
- Changing analytics/metrics response shapes that are cached.
- Sharing event contracts between Python/FastAPI and Kotlin/Spring services.

## Examples

The analytics cache key should include the response contract:

```python
f"{ANALYTICS_RESPONSE_SCHEMA_VERSION}:{tenant_id}:metrics:{start}:{end}"
```

The collection request should reject unsupported versions while retaining a default for old clients:

```python
schema_version: str = TRACKING_EVENT_SCHEMA_VERSION
```

## Related

- `app/events/registry.py`
- `app/events/schemas.py`
- `app/analytics/adapters/http.py`
- `tests/test_api.py`
