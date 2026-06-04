"""이벤트/응답 계약 버전 레지스트리.

서비스 간 공유되는 Published Language 의 버전을 한 곳에서 관리한다.
"""

from __future__ import annotations

TRACKING_EVENT_SCHEMA_VERSION = "tracking-event.v1"
ANALYTICS_RESPONSE_SCHEMA_VERSION = "analytics-response.v1"
PREDICTION_RESPONSE_SCHEMA_VERSION = "prediction-response.v1"
RECOMMENDATION_RESPONSE_SCHEMA_VERSION = "recommendation-response.v1"
AI_RESPONSE_SCHEMA_VERSION = "ai-response.v1"
AUDIENCE_RESPONSE_SCHEMA_VERSION = "audience-response.v1"
