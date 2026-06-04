"""AI Agent/Copy domain models.

The current implementation is deterministic and rule-based so every endpoint is
usable without an external LLM provider. Provider-backed generation can replace
the application rules later without changing the HTTP contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AiMetadata:
    model_version: str
    feature_version: str
    generated_at: datetime


@dataclass(frozen=True)
class SiteIssue:
    code: str
    severity: str
    segment: str
    summary: str
    evidence: str
    recommended_action: str


@dataclass(frozen=True)
class SiteDiagnosis:
    metadata: AiMetadata
    issues: tuple[SiteIssue, ...]
    health_score: float


@dataclass(frozen=True)
class CampaignSuggestion:
    audience: str
    channel: str
    message_goal: str
    trigger: str
    rationale: str
    priority: str


@dataclass(frozen=True)
class CampaignSuggestions:
    metadata: AiMetadata
    suggestions: tuple[CampaignSuggestion, ...]


@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    checks: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CopyCandidate:
    headline: str
    body: str
    call_to_action: str


@dataclass(frozen=True)
class CopyGeneration:
    metadata: AiMetadata
    guardrail: GuardrailResult
    requires_human_review: bool
    candidates: tuple[CopyCandidate, ...]
