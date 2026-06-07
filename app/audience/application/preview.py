"""Audience rule preview and materialization use cases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audience.application.templates import GetAudienceTemplate
from app.core.orm import AudienceMaterializationRow, EventRow
from app.prediction.application.scoring import (
    BuildVisitorPredictionFeatures,
    MultiHeadLogisticPredictionModel,
)
from app.prediction.domain.model import PredictionModelArtifact, VisitorFeatures


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
        rows = await self._session.execute(
            select(
                EventRow.visitor_id,
                EventRow.type,
                EventRow.amount,
                EventRow.occurred_at,
                EventRow.utm_medium,
                EventRow.utm_source,
                EventRow.category,
            ).where(
                EventRow.tenant_id == tenant_id,
                EventRow.occurred_at >= start,
                EventRow.occurred_at < end,
            )
        )
        profiles: dict[str, dict[str, Any]] = {}
        event_counts: dict[str, dict[str, int]] = {}
        for visitor_id, event_type, amount, occurred_at, utm_medium, utm_source, category in rows.all():
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
            event_counts.setdefault(visitor_id, {})[event_type] = (
                event_counts.setdefault(visitor_id, {}).get(event_type, 0) + 1
            )
            if event_type == "view":
                profile["view_count"] = int(profile["view_count"]) + 1
            if event_type == "cart_add":
                profile["cart_add_count"] = int(profile["cart_add_count"]) + 1
            if event_type == "purchase":
                profile["purchase_count"] = int(profile["purchase_count"]) + 1
                profile["total_revenue"] = float(profile["total_revenue"]) + float(amount or 0.0)
                if profile["last_purchase_at"] is None or occurred_at > profile["last_purchase_at"]:
                    profile["last_purchase_at"] = occurred_at

        unsupported: set[str] = set()
        matched: list[str] = []
        for visitor_id, profile in profiles.items():
            features = self._build_features(profile, end)
            scores = self._build_scores(visitor_id, features, end)
            if _evaluate_rule(
                rule,
                event_counts.get(visitor_id, {}),
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

    @staticmethod
    def _build_features(profile: dict[str, Any], end: datetime) -> dict[str, Any]:
        purchase_count = int(profile["purchase_count"])
        total_revenue = float(profile["total_revenue"])
        return {
            "first_seen_days_ago": _days_since(end, profile["first_seen_at"]),
            "days_since_last_seen": _days_since(end, profile["last_seen_at"]),
            "days_since_last_purchase": _days_since(end, profile["last_purchase_at"]),
            "purchase_count": purchase_count,
            "total_revenue": total_revenue,
            "avg_order_value": total_revenue / purchase_count if purchase_count else 0.0,
            "cart_amount": total_revenue,
            "cart_age_hours": _days_since(end, profile["last_seen_at"]) * 24,
            "view_count": int(profile["view_count"]),
            "cart_add_count": int(profile["cart_add_count"]),
            "utm_medium": profile.get("utm_medium") or "none",
            "utm_source": profile.get("utm_source") or "none",
            "top_category": profile.get("top_category"),
            "onsite_frequency_available": True,
        }

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
    profile: dict[str, Any],
    scores: dict[str, float],
    unsupported: set[str],
) -> bool:
    if "all" in rule:
        return all(
            _evaluate_condition(item, event_counts, profile, scores, unsupported)
            for item in rule["all"]
        )
    if "any" in rule:
        return any(
            _evaluate_condition(item, event_counts, profile, scores, unsupported)
            for item in rule["any"]
        )
    return _evaluate_condition(rule, event_counts, profile, scores, unsupported)


def _evaluate_condition(
    condition: dict[str, Any],
    event_counts: dict[str, int],
    profile: dict[str, Any],
    scores: dict[str, float],
    unsupported: set[str],
) -> bool:
    if "all" in condition or "any" in condition:
        return _evaluate_rule(condition, event_counts, profile, scores, unsupported)
    condition_type = condition.get("type")
    if condition_type == "event_count":
        actual = event_counts.get(str(condition.get("event")), 0)
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
