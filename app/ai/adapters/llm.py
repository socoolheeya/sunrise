"""AI 카피 생성 provider seam (Anti-Corruption Layer).

기본은 외부 의존성 없는 결정론적 rule-based provider 다. 운영에서 LLM 키가
설정되면 Anthropic provider 로 교체한다(lazy import — anthropic 미설치/미설정이면
자동으로 rule-based 로 graceful fallback). HTTP 계약은 동일하게 유지된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.ai.domain.model import CopyCandidate
from app.core.config import Settings


@dataclass(frozen=True)
class CopyContext:
    brand_tone: str
    campaign_goal: str
    product_name: str | None
    product_text: str | None
    image_url: str | None
    count: int


class LlmProvider(Protocol):
    name: str

    async def generate_copy(self, context: CopyContext) -> list[CopyCandidate]: ...


class RuleBasedLlmProvider:
    """결정론적 템플릿 기반 카피 생성(외부 의존성 없음)."""

    name = "rules"

    async def generate_copy(self, context: CopyContext) -> list[CopyCandidate]:
        subject = context.product_name or "recommended item"
        body_ctx = context.product_text or "your next purchase"
        tone = context.brand_tone.strip().lower()
        goal = context.campaign_goal.strip().lower()
        templates = (
            (
                f"{subject} is ready for you",
                f"{body_ctx}. A {tone} message for shoppers ready to {goal}.",
                "Shop now",
            ),
            (
                f"Come back for {subject}",
                f"Complete your {goal} with a clear next step and timely reminder.",
                "Continue",
            ),
            (
                f"Recommended: {subject}",
                f"Personalized for your interest in {body_ctx}.",
                "See recommendation",
            ),
        )
        return [
            CopyCandidate(headline=h, body=b, call_to_action=cta)
            for h, b, cta in templates[: context.count]
        ]


class AnthropicLlmProvider:
    """Anthropic Claude 기반 카피 생성. anthropic SDK 를 lazy import 한다."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def generate_copy(self, context: CopyContext) -> list[CopyCandidate]:
        # anthropic 은 운영 환경에서만 설치/설정되는 선택적 의존성이다.
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        client = AsyncAnthropic(api_key=self._api_key)
        prompt = (
            f"브랜드 톤: {context.brand_tone}\n캠페인 목적: {context.campaign_goal}\n"
            f"상품: {context.product_name or '-'}\n설명: {context.product_text or '-'}\n"
            f"위 정보로 마케팅 카피 후보 {context.count}개를 headline/body/CTA 로 생성."
        )
        # 실제 파싱/구조화는 운영 구현에서 tool-use(structured output)로 강제한다.
        message = await client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        _ = message  # 구조화 파싱은 운영 단계 구현. 계약 유지를 위해 seam 만 제공.
        raise NotImplementedError(
            "AnthropicLlmProvider response parsing is configured at deploy time"
        )


def create_llm_provider(settings: Settings) -> LlmProvider:
    """설정에 따라 provider 선택. anthropic 미설정/미설치 시 rule-based 로 fallback."""
    if settings.ai_llm_provider == "anthropic" and settings.anthropic_api_key:
        try:
            import anthropic  # noqa: F401, PLC0415

            return AnthropicLlmProvider(settings.anthropic_api_key, settings.ai_llm_model)
        except ImportError:
            return RuleBasedLlmProvider()
    return RuleBasedLlmProvider()
