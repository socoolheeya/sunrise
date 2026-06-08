"""Rule-based AI Agent and copy generation use cases."""

from __future__ import annotations

import re
from datetime import datetime

from app.ai.domain.model import (
    AiMetadata,
    CampaignSuggestion,
    CampaignSuggestions,
    CopyGeneration,
    GuardrailResult,
    SiteDiagnosis,
    SiteIssue,
)
from app.analytics.application.queries import GetCohort, GetDashboardMetrics, GetFunnel
from app.analytics.domain.repository import AnalyticsRepository

MODEL_VERSION = "rules.ai-agent-copy.v1"
FEATURE_VERSION = "analytics-lite.v1"


def metadata(generated_at: datetime) -> AiMetadata:
    return AiMetadata(
        model_version=MODEL_VERSION,
        feature_version=FEATURE_VERSION,
        generated_at=generated_at,
    )


def _priority(severity: str) -> str:
    return {"critical": "high", "warning": "medium"}.get(severity, "low")


class DiagnoseSite:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> SiteDiagnosis:
        metrics = await GetDashboardMetrics(self._repository).execute(
            tenant_id, start, end
        )
        funnel = await GetFunnel(self._repository).execute(tenant_id, start, end)
        cohort = await GetCohort(self._repository).execute(tenant_id, start, end)

        issues: list[SiteIssue] = []
        if metrics.visitor_count == 0:
            issues.append(
                SiteIssue(
                    code="no_traffic",
                    severity="critical",
                    segment="site",
                    summary="No visitor activity was observed in the selected window.",
                    evidence="visitor_count=0",
                    recommended_action=(
                        "Verify tracking installation and send a test event through "
                        "POST /v1/collect."
                    ),
                )
            )
        if metrics.visitor_count and metrics.cvr < 0.03:
            issues.append(
                SiteIssue(
                    code="low_conversion_rate",
                    severity="warning",
                    segment="checkout",
                    summary="Conversion rate is below the baseline threshold.",
                    evidence=f"cvr={metrics.cvr:.3f}",
                    recommended_action=(
                        "Create a cart or checkout recovery campaign for recent "
                        "high-intent visitors."
                    ),
                )
            )
        if len(funnel.steps) >= 3:
            cart_drop = funnel.drop_off(1)
            purchase_drop = funnel.drop_off(2)
            if cart_drop >= 0.5:
                issues.append(
                    SiteIssue(
                        code="view_to_cart_dropoff",
                        severity="warning",
                        segment="product_detail",
                        summary="Many visitors view products but do not add to cart.",
                        evidence=f"view_to_cart_drop_off={cart_drop:.3f}",
                        recommended_action=(
                            "Promote best-selling or recently viewed products with "
                            "onsite incentives."
                        ),
                    )
                )
            if purchase_drop >= 0.35:
                issues.append(
                    SiteIssue(
                        code="cart_to_purchase_dropoff",
                        severity="critical",
                        segment="cart",
                        summary="Cart visitors are not completing purchases.",
                        evidence=f"cart_to_purchase_drop_off={purchase_drop:.3f}",
                        recommended_action=(
                            "Run abandoned-cart messaging with urgency and clear "
                            "return-to-cart links."
                        ),
                    )
                )
        if metrics.purchase_count and metrics.repeat_rate < 0.2:
            issues.append(
                SiteIssue(
                    code="low_repeat_rate",
                    severity="warning",
                    segment="returning_customers",
                    summary="Repeat purchase rate is low for the selected window.",
                    evidence=f"repeat_rate={metrics.repeat_rate:.3f}",
                    recommended_action=(
                        "Launch a replenishment or cross-sell campaign after the "
                        "first purchase."
                    ),
                )
            )
        if cohort.rows and max((len(row.cells) for row in cohort.rows), default=0) <= 1:
            issues.append(
                SiteIssue(
                    code="limited_retention_signal",
                    severity="info",
                    segment="cohort",
                    summary="Retention matrix has limited post-purchase activity.",
                    evidence=f"cohort_rows={len(cohort.rows)}",
                    recommended_action=(
                        "Collect more post-purchase events or shorten the first "
                        "retention experiment window."
                    ),
                )
            )

        if not issues:
            issues.append(
                SiteIssue(
                    code="healthy_baseline",
                    severity="info",
                    segment="site",
                    summary="No major performance issue was detected.",
                    evidence=(
                        f"cvr={metrics.cvr:.3f}, repeat_rate="
                        f"{metrics.repeat_rate:.3f}"
                    ),
                    recommended_action=(
                        "Monitor benchmark deltas and test a small lift campaign."
                    ),
                )
            )

        penalty = sum(
            25 if issue.severity == "critical" else 12 if issue.severity == "warning" else 3
            for issue in issues
        )
        health_score = max(0.0, min(1.0, (100 - penalty) / 100))
        return SiteDiagnosis(
            metadata=metadata(end),
            issues=tuple(issues),
            health_score=health_score,
        )


class SuggestCampaigns:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        preferred_channels: tuple[str, ...],
        max_suggestions: int,
    ) -> CampaignSuggestions:
        diagnosis = await DiagnoseSite(self._repository).execute(tenant_id, start, end)
        channels = preferred_channels or ("kakao", "email", "onsite")
        suggestions: list[CampaignSuggestion] = []

        for index, issue in enumerate(diagnosis.issues):
            channel = channels[index % len(channels)]
            if issue.code == "cart_to_purchase_dropoff":
                audience = "Visitors who added to cart but did not purchase"
                goal = "recover_abandoned_cart"
                trigger = "cart_add without purchase in the selected window"
            elif issue.code == "view_to_cart_dropoff":
                audience = "Recent product viewers without cart activity"
                goal = "increase_cart_add"
                trigger = "view without cart_add"
            elif issue.code == "low_repeat_rate":
                audience = "First-time purchasers"
                goal = "drive_repeat_purchase"
                trigger = "purchase followed by no repeat purchase"
            elif issue.code == "no_traffic":
                audience = "Tracking QA audience"
                goal = "restore_event_collection"
                trigger = "no events collected"
            else:
                audience = "High-intent recent visitors"
                goal = "incremental_lift_test"
                trigger = issue.code

            suggestions.append(
                CampaignSuggestion(
                    audience=audience,
                    channel=channel,
                    message_goal=goal,
                    trigger=trigger,
                    rationale=issue.summary,
                    priority=_priority(issue.severity),
                )
            )

        return CampaignSuggestions(
            metadata=diagnosis.metadata,
            suggestions=tuple(suggestions[:max_suggestions]),
        )


_SENSITIVE_TERMS = ("guaranteed", "cure", "medical", "risk-free", "100% effective")
_PROFANITY = ("damn", "crap")
_PII_PATTERN = re.compile(
    r"(\b\d{3}-\d{3,4}-\d{4}\b|[\w.+-]+@[\w-]+\.[\w.-]+)"
)


def run_copy_guardrail(
    brand_tone: str,
    campaign_goal: str,
    product_text: str | None,
    image_url: str | None,
) -> GuardrailResult:
    """카피 입력/맥락에 대한 카테고리별 안전 점검."""
    checks = ("non_empty_inputs", "prohibited_claims", "pii", "profanity", "media")
    reasons: list[str] = []
    text = f"{brand_tone} {campaign_goal} {product_text or ''}"
    lower = text.lower()
    if not brand_tone.strip() or not campaign_goal.strip():
        reasons.append("non_empty_inputs: brand_tone and campaign_goal are required")
    if any(term in lower for term in _SENSITIVE_TERMS):
        reasons.append("prohibited_claims: sensitive or absolute claim detected")
    if _PII_PATTERN.search(text):
        reasons.append("pii: contact information detected in copy inputs")
    if any(term in lower for term in _PROFANITY):
        reasons.append("profanity: inappropriate language detected")
    if image_url and not image_url.startswith(("http://", "https://")):
        reasons.append("media: image_url must be http or https when provided")
    return GuardrailResult(passed=not reasons, checks=checks, reasons=tuple(reasons))


class GenerateCopy:
    """카피 생성: provider(rule/LLM)로 후보 생성 + 가드레일 + human-review 플래그."""

    def __init__(self, provider) -> None:
        self._provider = provider

    async def execute(
        self,
        brand_tone: str,
        campaign_goal: str,
        product_name: str | None,
        product_text: str | None,
        image_url: str | None,
        count: int,
        generated_at: datetime,
    ) -> CopyGeneration:
        from app.ai.adapters.llm import CopyContext

        guardrail = run_copy_guardrail(brand_tone, campaign_goal, product_text, image_url)
        candidates = await self._provider.generate_copy(
            CopyContext(
                brand_tone=brand_tone,
                campaign_goal=campaign_goal,
                product_name=product_name,
                product_text=product_text,
                image_url=image_url,
                count=count,
            )
        )
        return CopyGeneration(
            metadata=metadata(generated_at),
            guardrail=guardrail,
            # 가드레일 위반 또는 이미지 동반 생성은 사람 검토 필요.
            requires_human_review=not guardrail.passed or image_url is not None,
            candidates=tuple(candidates),
        )
