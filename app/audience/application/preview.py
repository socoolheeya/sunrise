"""Audience rule preview and materialization use cases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audience.application.templates import GetAudienceTemplate
from app.core.orm import AudienceMaterializationRow, EventRow, ProductFeatureRow
from app.prediction.application.scoring import (
    BuildVisitorPredictionFeatures,
    MultiHeadLogisticPredictionModel,
)
from app.prediction.domain.model import PredictionModelArtifact, VisitorFeatures

_HIGH_MARGIN_THRESHOLD = 0.4
_NO_REPURCHASE_SENTINEL = 9999  # 구매 이력<2 → 재구매 주기 미정의

# 이 배포에서 이벤트/상품 feature 로 평가 가능한 profile 필드.
SUPPORTED_PROFILE_FIELDS = frozenset({
    # 기본(이벤트 집계)
    "first_seen_days_ago", "days_since_last_seen", "days_since_last_purchase",
    "purchase_count", "total_revenue", "avg_order_value", "cart_amount",
    "cart_age_hours", "view_count", "cart_add_count", "utm_medium", "utm_source",
    "top_category", "onsite_frequency_available",
    # 이벤트 도출 (P1-3)
    "category_purchase_count", "cross_sell_score", "engagement_score",
    "expected_repurchase_days", "recent_campaign_exposure_days", "lifetime_value",
    # 상품 feature join (P1-3, 상품 feature 적재 시)
    "full_price_purchase_count", "high_margin_purchase_count",
    "discount_affinity", "premium_affinity", "return_rate",
})

# 이벤트/상품 신호가 없어 외부 프로필·동의·쿠폰·배송 소스가 필요한 필드.
EXTERNAL_PROFILE_FIELDS = frozenset({
    "review_written",
    "coupon_affinity", "coupon_purchase_count", "coupon_usage_rate",
    "free_shipping_affinity", "free_shipping_gap",
    "kakao_opt_in", "email_opt_in", "sms_opt_in",
    "email_verified", "phone_verified",
})


@dataclass(frozen=True)
class _ProductFeat:
    category: str | None
    price: float | None
    original_price: float | None
    gross_margin: float | None
    return_rate: float | None


def _is_full_price(feat: _ProductFeat) -> bool:
    if feat.price is None:
        return False
    if feat.original_price is None:
        return True
    return feat.price >= feat.original_price


def _is_high_margin(feat: _ProductFeat) -> bool:
    margin = feat.gross_margin
    if margin is None:
        return False
    if 0 <= margin <= 1:
        return margin >= _HIGH_MARGIN_THRESHOLD
    if feat.price and feat.price > 0:
        return margin / feat.price >= _HIGH_MARGIN_THRESHOLD
    return False


def _collect_profile_fields(rule: dict[str, Any], out: set[str]) -> None:
    """rule 트리에서 참조하는 profile 필드명을 수집."""
    for key in ("all", "any"):
        if key in rule:
            for item in rule[key]:
                _collect_profile_fields(item, out)
            return
    if rule.get("type") == "profile":
        out.add(str(rule.get("field")))


def template_external_fields(rule: dict[str, Any]) -> list[str]:
    """템플릿 rule 이 참조하는 외부 소스 필요(미지원) 필드 목록."""
    fields: set[str] = set()
    _collect_profile_fields(rule, fields)
    return sorted(f for f in fields if f in EXTERNAL_PROFILE_FIELDS)


@dataclass(frozen=True)
class AudiencePreview:
    rule_hash: str
    matched_count: int
    sample_visitor_ids: tuple[str, ...]
    unsupported_conditions: tuple[str, ...]
    evaluated_at: datetime


@dataclass(frozen=True)
class AudienceMaterialization:
    audience_id: str
    rule_hash: str
    member_count: int
    sample_visitor_ids: tuple[str, ...]
    status: str
    as_of: datetime


def canonical_rule(rule: dict[str, Any]) -> str:
    return json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rule_hash(rule: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_rule(rule).encode("utf-8")).hexdigest()


def resolve_rule(template_id: str | None, rule: dict[str, Any] | None) -> dict[str, Any]:
    if rule is not None:
        return rule
    if template_id is None:
        raise ValueError("template_id or rule is required")
    template = GetAudienceTemplate().execute(template_id)
    if template is None:
        raise ValueError("audience template not found")
    return template.rule


class AudienceRuleEvaluator:
    def __init__(
        self,
        session: AsyncSession,
        prediction_model_artifact: PredictionModelArtifact | None = None,
    ) -> None:
        self._session = session
        self._prediction_model_artifact = prediction_model_artifact

    async def preview(
        self,
        tenant_id: str,
        rule: dict[str, Any],
        start: datetime,
        end: datetime,
        *,
        sample_limit: int = 20,
    ) -> AudiencePreview:
        product_features, category_avg_price = await self._load_product_features(tenant_id)
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.type,
                EventRow.amount,
                EventRow.occurred_at,
                EventRow.utm_medium,
                EventRow.utm_source,
                EventRow.category,
                EventRow.product_id,
            ).where(
                EventRow.tenant_id == tenant_id,
                EventRow.occurred_at >= start,
                EventRow.occurred_at < end,
            )
        )
        profiles: dict[str, dict[str, Any]] = {}
        event_counts: dict[str, dict[str, int]] = {}
        event_times: dict[str, dict[str, list[datetime]]] = {}
        for (
            visitor_id, event_type, amount, occurred_at,
            utm_medium, utm_source, category, product_id,
        ) in rows.all():
            profile = profiles.setdefault(
                visitor_id,
                {
                    "first_seen_at": occurred_at,
                    "last_seen_at": occurred_at,
                    "last_purchase_at": None,
                    "purchase_count": 0,
                    "total_revenue": 0.0,
                    "cart_add_count": 0,
                    "view_count": 0,
                    "utm_medium": utm_medium,
                    "utm_source": utm_source,
                    "top_category": category,
                    "purchased_product_ids": [],
                    "interacted_product_ids": set(),
                    "purchased_categories": set(),
                    "purchase_times": [],
                    "campaign_times": [],
                },
            )
            if occurred_at < profile["first_seen_at"]:
                profile["first_seen_at"] = occurred_at
            if occurred_at > profile["last_seen_at"]:
                profile["last_seen_at"] = occurred_at
            if utm_medium:
                profile["utm_medium"] = utm_medium
            if utm_source:
                profile["utm_source"] = utm_source
            if category:
                profile["top_category"] = category
            if product_id and event_type in {"view", "cart_add", "purchase"}:
                profile["interacted_product_ids"].add(product_id)
            event_counts.setdefault(visitor_id, {})[event_type] = (
                event_counts.setdefault(visitor_id, {}).get(event_type, 0) + 1
            )
            event_times.setdefault(visitor_id, {}).setdefault(event_type, []).append(
                occurred_at
            )
            if event_type == "view":
                profile["view_count"] = int(profile["view_count"]) + 1
            if event_type == "cart_add":
                profile["cart_add_count"] = int(profile["cart_add_count"]) + 1
            if event_type in {"campaign_impression", "campaign_click"}:
                profile["campaign_times"].append(occurred_at)
            if event_type == "purchase":
                profile["purchase_count"] = int(profile["purchase_count"]) + 1
                profile["total_revenue"] = float(profile["total_revenue"]) + float(amount or 0.0)
                profile["purchase_times"].append(occurred_at)
                if product_id:
                    profile["purchased_product_ids"].append(product_id)
                if category:
                    profile["purchased_categories"].add(category)
                if profile["last_purchase_at"] is None or occurred_at > profile["last_purchase_at"]:
                    profile["last_purchase_at"] = occurred_at

        unsupported: set[str] = set()
        matched: list[str] = []
        for visitor_id, profile in profiles.items():
            features = self._build_features(
                profile, end, product_features, category_avg_price
            )
            scores = self._build_scores(visitor_id, features, end)
            if _evaluate_rule(
                rule,
                event_counts.get(visitor_id, {}),
                event_times.get(visitor_id, {}),
                end,
                features,
                scores,
                unsupported,
            ):
                matched.append(visitor_id)

        return AudiencePreview(
            rule_hash=rule_hash(rule),
            matched_count=len(matched),
            sample_visitor_ids=tuple(matched[:sample_limit]),
            unsupported_conditions=tuple(sorted(unsupported)),
            evaluated_at=datetime.now(timezone.utc),
        )

    async def materialize(
        self,
        tenant_id: str,
        audience_id: str,
        rule: dict[str, Any],
        start: datetime,
        end: datetime,
        *,
        sample_limit: int = 50,
    ) -> AudienceMaterialization:
        preview = await self.preview(
            tenant_id,
            rule,
            start,
            end,
            sample_limit=sample_limit,
        )
        now = datetime.now(timezone.utc)
        values = {
            "tenant_id": tenant_id,
            "audience_id": audience_id,
            "rule_hash": preview.rule_hash,
            "rule_json": canonical_rule(rule),
            "member_count": preview.matched_count,
            "sample_visitor_ids_json": json.dumps(list(preview.sample_visitor_ids)),
            "status": "active",
            "as_of": preview.evaluated_at,
            "created_at": now,
        }
        bind = self._session.get_bind()
        if bind.dialect.name == "sqlite":
            stmt = sqlite_insert(AudienceMaterializationRow).values(**values)
        else:
            stmt = pg_insert(AudienceMaterializationRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "audience_id"],
            set_={
                "rule_hash": values["rule_hash"],
                "rule_json": values["rule_json"],
                "member_count": values["member_count"],
                "sample_visitor_ids_json": values["sample_visitor_ids_json"],
                "status": values["status"],
                "as_of": values["as_of"],
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return AudienceMaterialization(
            audience_id=audience_id,
            rule_hash=preview.rule_hash,
            member_count=preview.matched_count,
            sample_visitor_ids=preview.sample_visitor_ids,
            status="active",
            as_of=preview.evaluated_at,
        )

    async def _load_product_features(
        self, tenant_id: str
    ) -> tuple[dict[str, _ProductFeat], dict[str, float]]:
        rows = await self._session.execute(
            select(
                ProductFeatureRow.product_id,
                ProductFeatureRow.category,
                ProductFeatureRow.price,
                ProductFeatureRow.original_price,
                ProductFeatureRow.gross_margin,
                ProductFeatureRow.return_rate,
            ).where(ProductFeatureRow.tenant_id == tenant_id)
        )
        features: dict[str, _ProductFeat] = {}
        category_prices: dict[str, list[float]] = {}
        for product_id, category, price, original_price, gross_margin, return_rate in rows.all():
            features[product_id] = _ProductFeat(
                category=category,
                price=price,
                original_price=original_price,
                gross_margin=gross_margin,
                return_rate=return_rate,
            )
            if category and price is not None:
                category_prices.setdefault(category, []).append(float(price))
        category_avg_price = {
            category: sum(prices) / len(prices)
            for category, prices in category_prices.items()
        }
        return features, category_avg_price

    @staticmethod
    def _build_features(
        profile: dict[str, Any],
        end: datetime,
        product_features: dict[str, _ProductFeat],
        category_avg_price: dict[str, float],
    ) -> dict[str, Any]:
        purchase_count = int(profile["purchase_count"])
        total_revenue = float(profile["total_revenue"])
        view_count = int(profile["view_count"])
        cart_add_count = int(profile["cart_add_count"])

        # ---- 이벤트 도출 (P1-3) ----
        purchase_times = sorted(profile.get("purchase_times", []))
        if len(purchase_times) >= 2:
            gaps = [
                (purchase_times[i] - purchase_times[i - 1]).days
                for i in range(1, len(purchase_times))
            ]
            expected_repurchase_days = int(median(gaps))
        else:
            expected_repurchase_days = _NO_REPURCHASE_SENTINEL
        campaign_times = profile.get("campaign_times", [])
        recent_campaign_exposure_days = (
            _days_since(end, max(campaign_times)) if campaign_times else 365
        )
        engagement_score = round(
            min(1.0, view_count / 20) * 0.5
            + min(1.0, cart_add_count / 8) * 0.3
            + min(1.0, purchase_count / 4) * 0.2,
            4,
        )
        distinct_categories = len(profile.get("purchased_categories", set()))
        cross_sell_score = round(min(1.0, max(0, distinct_categories - 1) / 2), 4)

        features: dict[str, Any] = {
            "first_seen_days_ago": _days_since(end, profile["first_seen_at"]),
            "days_since_last_seen": _days_since(end, profile["last_seen_at"]),
            "days_since_last_purchase": _days_since(end, profile["last_purchase_at"]),
            "purchase_count": purchase_count,
            "total_revenue": total_revenue,
            "avg_order_value": total_revenue / purchase_count if purchase_count else 0.0,
            "cart_amount": total_revenue,
            "cart_age_hours": _days_since(end, profile["last_seen_at"]) * 24,
            "view_count": view_count,
            "cart_add_count": cart_add_count,
            "utm_medium": profile.get("utm_medium") or "none",
            "utm_source": profile.get("utm_source") or "none",
            "top_category": profile.get("top_category"),
            "onsite_frequency_available": True,
            "category_purchase_count": distinct_categories,
            "cross_sell_score": cross_sell_score,
            "engagement_score": engagement_score,
            "expected_repurchase_days": expected_repurchase_days,
            "recent_campaign_exposure_days": recent_campaign_exposure_days,
            "lifetime_value": total_revenue,  # 누적 구매금액 기준 LTV proxy
        }

        # ---- 상품 feature join (P1-3, 상품 feature 적재 시에만) ----
        interacted = [
            product_features[pid]
            for pid in profile.get("interacted_product_ids", set())
            if pid in product_features
        ]
        purchased = [
            product_features[pid]
            for pid in profile.get("purchased_product_ids", [])
            if pid in product_features
        ]
        if interacted:
            priced = [f for f in interacted if f.price is not None]
            if priced:
                discounted = sum(
                    1 for f in priced
                    if f.original_price is not None and f.original_price > f.price
                )
                features["discount_affinity"] = round(discounted / len(priced), 4)
                premium_pool = [
                    f for f in priced
                    if f.category and category_avg_price.get(f.category)
                ]
                if premium_pool:
                    premium = sum(
                        1 for f in premium_pool
                        if f.price > category_avg_price[f.category]
                    )
                    features["premium_affinity"] = round(premium / len(premium_pool), 4)
        if purchased:
            features["full_price_purchase_count"] = sum(
                1 for f in purchased if _is_full_price(f)
            )
            features["high_margin_purchase_count"] = sum(
                1 for f in purchased if _is_high_margin(f)
            )
            return_rates = [f.return_rate for f in purchased if f.return_rate is not None]
            if return_rates:
                features["return_rate"] = round(
                    sum(return_rates) / len(return_rates), 4
                )
        # 위 product-join 필드는 데이터 없으면 omit → 해당 조건은 unsupported 처리.
        return features

    def _build_scores(
        self,
        visitor_id: str,
        features: dict[str, Any],
        end: datetime,
    ) -> dict[str, float]:
        if self._prediction_model_artifact is not None:
            raw = VisitorFeatures(
                visitor_id=visitor_id,
                view_count=int(features["view_count"]),
                cart_add_count=int(features["cart_add_count"]),
                purchase_count=int(features["purchase_count"]),
                revenue=float(features["total_revenue"]),
                last_seen_at=end - timedelta(days=int(features["days_since_last_seen"])),
                last_purchase_at=(
                    None
                    if int(features["days_since_last_purchase"]) >= 365
                    else end - timedelta(days=int(features["days_since_last_purchase"]))
                ),
            )
            built = BuildVisitorPredictionFeatures().execute(raw, end)
            model = MultiHeadLogisticPredictionModel(self._prediction_model_artifact)
            purchase_score = model.predict_visitor("purchase_score", built)
            churn_risk = model.predict_visitor("churn_risk", built)
            affinity = round(min(1.0, int(features["view_count"]) / 5), 4)
            return {
                "purchase_score": purchase_score,
                "churn_risk": churn_risk,
                "category_affinity": affinity,
                "product_affinity": affinity,
                "next_best_offer": round(purchase_score * 0.9, 4),
            }
        purchase_score = min(
            1.0,
            0.08 * features["view_count"]
            + 0.18 * features["cart_add_count"]
            + 0.30 * features["purchase_count"]
            + 0.20 * (1 if features["days_since_last_seen"] <= 7 else 0),
        )
        churn_risk = min(1.0, features["days_since_last_seen"] / 90)
        return {
            "purchase_score": round(purchase_score, 4),
            "churn_risk": round(churn_risk, 4),
            "category_affinity": round(min(1.0, features["view_count"] / 5), 4),
            "product_affinity": round(min(1.0, features["view_count"] / 5), 4),
            "next_best_offer": round(purchase_score * 0.9, 4),
        }


def _as_aware(value: datetime, reference: datetime) -> datetime:
    """naive/aware 혼용(예: sqlite naive vs UTC aware) 비교를 안전하게 정렬."""
    if reference.tzinfo is not None and value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    if reference.tzinfo is None and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _days_since(reference: datetime, value: datetime | None) -> int:
    if value is None:
        return 365
    if reference.tzinfo is not None and value.tzinfo is None:
        value = value.replace(tzinfo=reference.tzinfo)
    if reference.tzinfo is None and value.tzinfo is not None:
        reference = reference.replace(tzinfo=value.tzinfo)
    return max(0, (reference - value).days)


def _evaluate_rule(
    rule: dict[str, Any],
    event_counts: dict[str, int],
    event_times: dict[str, list[datetime]],
    end: datetime,
    profile: dict[str, Any],
    scores: dict[str, float],
    unsupported: set[str],
) -> bool:
    if "all" in rule:
        return all(
            _evaluate_condition(
                item, event_counts, event_times, end, profile, scores, unsupported
            )
            for item in rule["all"]
        )
    if "any" in rule:
        return any(
            _evaluate_condition(
                item, event_counts, event_times, end, profile, scores, unsupported
            )
            for item in rule["any"]
        )
    return _evaluate_condition(
        rule, event_counts, event_times, end, profile, scores, unsupported
    )


def _evaluate_condition(
    condition: dict[str, Any],
    event_counts: dict[str, int],
    event_times: dict[str, list[datetime]],
    end: datetime,
    profile: dict[str, Any],
    scores: dict[str, float],
    unsupported: set[str],
) -> bool:
    if "all" in condition or "any" in condition:
        return _evaluate_rule(
            condition, event_counts, event_times, end, profile, scores, unsupported
        )
    condition_type = condition.get("type")
    if condition_type == "event_count":
        event = str(condition.get("event"))
        window_days = condition.get("window_days")
        if window_days is None:
            actual = event_counts.get(event, 0)
        else:
            # window_days 가 지정되면 [end - window_days, end] 하위 윈도우 내
            # 이벤트만 센다. (예: 24시간 장바구니 이탈 vs 7일 이탈 구분)
            cutoff = end - timedelta(days=int(window_days))
            actual = sum(
                1
                for occurred_at in event_times.get(event, [])
                if _as_aware(occurred_at, end) >= cutoff
            )
    elif condition_type == "profile":
        field = str(condition.get("field"))
        if field not in profile:
            unsupported.add(f"profile:{field}")
            return False
        actual = profile[field]
    elif condition_type == "score":
        name = str(condition.get("name"))
        if name not in scores:
            unsupported.add(f"score:{name}")
            return False
        actual = scores[name]
    else:
        unsupported.add(str(condition_type or "unknown"))
        return False
    return _compare(actual, str(condition.get("op", "gte")), condition.get("value"))


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "in":
        return actual in set(expected or [])
    try:
        left = float(actual)
        right = float(expected)
    except (TypeError, ValueError):
        left = str(actual)
        right = str(expected)
    if op == "gte":
        return left >= right
    if op == "gt":
        return left > right
    if op == "lte":
        return left <= right
    if op == "lt":
        return left < right
    return False
