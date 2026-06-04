"""Versioned ecommerce audience template catalog."""

from __future__ import annotations

from app.audience.domain.model import AudienceTemplate

CATALOG_VERSION = "audience-template-catalog.v1"


def _rule(all_: list[dict] | None = None, any_: list[dict] | None = None) -> dict:
    payload: dict[str, list[dict]] = {}
    if all_:
        payload["all"] = all_
    if any_:
        payload["any"] = any_
    return payload


def _event(name: str, days: int, op: str = "gte", value: int = 1) -> dict:
    return {"type": "event_count", "event": name, "window_days": days, "op": op, "value": value}


def _score(name: str, op: str, value: float) -> dict:
    return {"type": "score", "name": name, "op": op, "value": value}


def _profile(field: str, op: str, value) -> dict:
    return {"type": "profile", "field": field, "op": op, "value": value}


def _template(
    template_id: str,
    name: str,
    category: str,
    description: str,
    rule: dict,
    channels: tuple[str, ...],
    trigger: str,
    tags: tuple[str, ...],
) -> AudienceTemplate:
    return AudienceTemplate(
        template_id=template_id,
        name=name,
        category=category,
        description=description,
        rule=rule,
        recommended_channels=channels,
        recommended_trigger=trigger,
        tags=tags,
    )


AUDIENCE_TEMPLATES: tuple[AudienceTemplate, ...] = (
    _template("first_visit_today", "오늘 첫 방문 고객", "acquisition", "오늘 처음 방문한 신규 방문자입니다.", _rule([_event("view", 1), _profile("first_seen_days_ago", "lte", 1)]), ("onsite",), "first_visit", ("new-visitor", "onsite")),
    _template("new_visitor_no_cart", "신규 방문 후 장바구니 없음", "acquisition", "신규 방문했지만 아직 장바구니 행동이 없는 고객입니다.", _rule([_profile("first_seen_days_ago", "lte", 7), _event("cart_add", 7, "eq", 0)]), ("onsite", "email"), "view_without_cart", ("new-visitor", "cart")),
    _template("new_visitor_high_intent", "신규 고의도 방문자", "acquisition", "신규 고객 중 구매 가능성이 높은 그룹입니다.", _rule([_profile("first_seen_days_ago", "lte", 14), _score("purchase_score", "gte", 0.7)]), ("onsite", "kakao"), "high_intent_new_visitor", ("new-visitor", "purchase-score")),
    _template("organic_first_session", "첫 유입 오가닉 방문자", "acquisition", "광고가 아닌 자연 유입 첫 세션 고객입니다.", _rule([_profile("first_seen_days_ago", "lte", 7), _profile("utm_medium", "in", ["organic", "none"])]), ("onsite",), "organic_first_session", ("organic", "new-visitor")),
    _template("paid_first_session", "광고 첫 유입 방문자", "acquisition", "광고로 처음 유입되어 전환 유도가 필요한 고객입니다.", _rule([_profile("first_seen_days_ago", "lte", 7), _profile("utm_medium", "in", ["cpc", "paid", "display"])]), ("onsite", "kakao"), "paid_first_session", ("paid", "new-visitor")),
    _template("landing_bounce_risk", "랜딩 이탈 위험 고객", "acquisition", "랜딩 이후 추가 탐색이 약한 고객입니다.", _rule([_event("view", 1, "lte", 1), _score("purchase_score", "lt", 0.3)]), ("onsite",), "bounce_prevention", ("bounce", "onsite")),
    _template("category_browser_7d", "최근 카테고리 탐색 고객", "browse", "최근 7일 안에 카테고리를 탐색한 고객입니다.", _rule([_event("category_view", 7)]), ("onsite", "email"), "category_browse", ("category", "browse")),
    _template("product_view_no_cart_1d", "상품 조회 후 장바구니 없음", "browse", "최근 상품을 봤지만 장바구니에 담지 않은 고객입니다.", _rule([_event("view", 1), _event("cart_add", 1, "eq", 0)]), ("onsite",), "view_without_cart", ("product-view", "cart")),
    _template("product_view_no_cart_7d", "7일 상품 조회 후 장바구니 없음", "browse", "7일 내 상품 조회는 있으나 장바구니가 없는 고객입니다.", _rule([_event("view", 7), _event("cart_add", 7, "eq", 0)]), ("email", "kakao"), "browse_retargeting", ("product-view", "retargeting")),
    _template("multi_product_viewer", "여러 상품 비교 고객", "browse", "여러 상품을 비교하며 탐색 중인 고객입니다.", _rule([_profile("distinct_viewed_products_7d", "gte", 3)]), ("onsite", "email"), "comparison_assist", ("comparison", "browse")),
    _template("same_product_repeat_view", "동일 상품 반복 조회 고객", "browse", "같은 상품을 반복 조회해 구매 망설임이 있는 고객입니다.", _rule([_profile("max_product_views_7d", "gte", 3)]), ("onsite", "kakao"), "hesitation_assist", ("hesitation", "product-view")),
    _template("price_sensitive_browser", "가격 민감 탐색 고객", "browse", "할인 상품이나 낮은 가격대 상품에 반응한 고객입니다.", _rule([_profile("discount_product_views_14d", "gte", 2)]), ("onsite", "email"), "discount_nudge", ("discount", "browse")),
    _template("brand_interest_browser", "브랜드 관심 고객", "browse", "특정 브랜드 상품을 반복 탐색한 고객입니다.", _rule([_profile("top_brand_views_30d", "gte", 3)]), ("email", "onsite"), "brand_interest", ("brand", "browse")),
    _template("search_no_purchase", "검색 후 미구매 고객", "browse", "검색 행동은 있으나 구매하지 않은 고객입니다.", _rule([_event("search", 7), _event("purchase", 7, "eq", 0)]), ("onsite", "email"), "search_retargeting", ("search", "no-purchase")),
    _template("cart_added_no_purchase_1h", "1시간 장바구니 이탈", "cart", "장바구니 담기 후 1시간 내 구매가 없는 고객입니다.", _rule([_profile("cart_age_hours", "gte", 1), _event("purchase", 1, "eq", 0)]), ("onsite", "kakao"), "cart_recovery", ("cart", "abandonment")),
    _template("cart_added_no_purchase_24h", "24시간 장바구니 이탈", "cart", "장바구니 담기 후 24시간 내 구매가 없는 고객입니다.", _rule([_profile("cart_age_hours", "gte", 24), _event("purchase", 1, "eq", 0)]), ("kakao", "email"), "cart_recovery", ("cart", "abandonment")),
    _template("cart_value_high", "고액 장바구니 고객", "cart", "장바구니 금액이 높은 전환 우선 고객입니다.", _rule([_profile("cart_amount", "gte", 200000)]), ("kakao", "onsite"), "high_value_cart", ("cart", "high-value")),
    _template("cart_value_low", "소액 장바구니 고객", "cart", "무료배송/묶음구매 유도가 가능한 소액 장바구니 고객입니다.", _rule([_profile("cart_amount", "lt", 50000), _event("cart_add", 7)]), ("onsite", "email"), "basket_builder", ("cart", "aov")),
    _template("cart_remove_recent", "최근 장바구니 제거 고객", "cart", "상품을 장바구니에서 제거한 고객입니다.", _rule([_event("cart_remove", 7)]), ("email", "onsite"), "cart_remove_recovery", ("cart-remove", "recovery")),
    _template("checkout_started_no_purchase", "주문서 이탈 고객", "cart", "주문서 진입 후 구매 완료가 없는 고객입니다.", _rule([_event("checkout_start", 3), _event("purchase", 3, "eq", 0)]), ("kakao", "sms"), "checkout_recovery", ("checkout", "abandonment")),
    _template("free_shipping_gap", "무료배송 임박 고객", "cart", "무료배송 기준까지 추가 구매 여지가 있는 고객입니다.", _rule([_profile("free_shipping_gap", "gt", 0), _profile("free_shipping_gap", "lte", 20000)]), ("onsite",), "free_shipping_nudge", ("cart", "free-shipping")),
    _template("first_purchase_completed", "첫 구매 완료 고객", "purchase", "첫 구매를 완료한 고객입니다.", _rule([_profile("purchase_count", "eq", 1)]), ("kakao", "email"), "first_purchase_thank_you", ("first-purchase", "post-purchase")),
    _template("recent_purchase_7d", "최근 7일 구매 고객", "purchase", "최근 7일 내 구매한 고객입니다.", _rule([_event("purchase", 7)]), ("email", "kakao"), "post_purchase", ("purchase", "recent")),
    _template("repeat_purchase_customer", "반복 구매 고객", "purchase", "2회 이상 구매한 고객입니다.", _rule([_profile("purchase_count", "gte", 2)]), ("kakao", "email"), "repeat_customer", ("repeat", "purchase")),
    _template("high_aov_customer", "고객 객단가 상위 그룹", "purchase", "평균 주문금액이 높은 고객입니다.", _rule([_profile("avg_order_value", "gte", 150000)]), ("kakao", "email"), "vip_value", ("aov", "vip")),
    _template("low_aov_cross_sell", "소액 구매 크로스셀 대상", "purchase", "소액 구매 후 추가 구매 유도가 필요한 고객입니다.", _rule([_profile("avg_order_value", "lt", 50000), _profile("purchase_count", "gte", 1)]), ("email", "onsite"), "cross_sell", ("aov", "cross-sell")),
    _template("category_first_purchase", "카테고리 첫 구매 고객", "purchase", "특정 카테고리를 처음 구매한 고객입니다.", _rule([_profile("category_purchase_count", "eq", 1)]), ("email", "kakao"), "category_onboarding", ("category", "first-purchase")),
    _template("coupon_used_purchase", "쿠폰 구매 고객", "purchase", "쿠폰 사용 구매 경험이 있는 고객입니다.", _rule([_profile("coupon_purchase_count", "gte", 1)]), ("kakao", "email"), "coupon_follow_up", ("coupon", "purchase")),
    _template("full_price_purchase", "정가 구매 고객", "purchase", "할인 없이 구매한 마진 우호 고객입니다.", _rule([_profile("full_price_purchase_count", "gte", 1)]), ("email", "onsite"), "premium_follow_up", ("margin", "purchase")),
    _template("replenishment_due", "재구매 주기 도래 고객", "retention", "예상 재구매 주기가 도래한 고객입니다.", _rule([_profile("days_since_last_purchase", "gte", 25), _profile("expected_repurchase_days", "lte", 30)]), ("kakao", "email"), "replenishment", ("retention", "repurchase")),
    _template("post_purchase_cross_sell", "구매 후 크로스셀 대상", "retention", "구매 후 함께 살 만한 상품 추천 대상입니다.", _rule([_event("purchase", 14), _profile("cross_sell_score", "gte", 0.5)]), ("email", "onsite"), "cross_sell", ("cross-sell", "post-purchase")),
    _template("review_request_due", "리뷰 요청 대상", "retention", "배송/사용 후 리뷰 요청 타이밍이 된 고객입니다.", _rule([_profile("days_since_last_purchase", "gte", 7), _profile("review_written", "eq", False)]), ("kakao", "email"), "review_request", ("review", "post-purchase")),
    _template("loyal_active_90d", "90일 활성 충성 고객", "retention", "최근 90일 구매와 방문이 모두 있는 충성 고객입니다.", _rule([_event("purchase", 90), _event("view", 30)]), ("kakao", "email"), "loyalty_reward", ("loyalty", "active")),
    _template("membership_upgrade_candidate", "멤버십 업그레이드 후보", "retention", "구매 빈도와 금액이 멤버십 전환 기준에 근접한 고객입니다.", _rule([_profile("purchase_count", "gte", 3), _profile("total_revenue", "gte", 300000)]), ("kakao", "email"), "membership_upgrade", ("membership", "loyalty")),
    _template("winback_30d_no_visit", "30일 미방문 회복 대상", "churn", "최근 30일 방문이 없는 고객입니다.", _rule([_profile("days_since_last_seen", "gte", 30)]), ("kakao", "email"), "winback", ("churn", "inactive")),
    _template("winback_60d_no_purchase", "60일 미구매 회복 대상", "churn", "최근 60일 구매가 없는 고객입니다.", _rule([_profile("days_since_last_purchase", "gte", 60), _profile("purchase_count", "gte", 1)]), ("kakao", "email"), "purchase_winback", ("churn", "purchase")),
    _template("high_churn_risk", "이탈 위험 고점수 고객", "churn", "이탈 위험 점수가 높은 고객입니다.", _rule([_score("churn_risk", "gte", 0.7)]), ("kakao", "email"), "churn_prevention", ("churn-risk", "ml")),
    _template("vip_churn_risk", "VIP 이탈 위험 고객", "churn", "가치가 높고 이탈 위험도 높은 고객입니다.", _rule([_profile("lifetime_value", "gte", 500000), _score("churn_risk", "gte", 0.6)]), ("kakao", "sms"), "vip_save", ("vip", "churn")),
    _template("dormant_coupon_candidate", "휴면 쿠폰 대상", "churn", "휴면 상태에서 쿠폰 반응 가능성이 있는 고객입니다.", _rule([_profile("days_since_last_seen", "gte", 45), _profile("coupon_affinity", "gte", 0.5)]), ("kakao", "email"), "dormant_coupon", ("dormant", "coupon")),
    _template("low_engagement_recent", "최근 저관여 고객", "churn", "최근 방문은 있으나 상품 관여가 낮은 고객입니다.", _rule([_event("view", 14), _profile("engagement_score", "lt", 0.3)]), ("onsite", "email"), "engagement_lift", ("engagement", "churn")),
    _template("vip_lifetime_value", "LTV VIP 고객", "loyalty", "누적 구매금액이 높은 VIP 고객입니다.", _rule([_profile("lifetime_value", "gte", 1000000)]), ("kakao", "sms"), "vip_exclusive", ("vip", "ltv")),
    _template("top_frequency_buyer", "구매 빈도 상위 고객", "loyalty", "구매 횟수가 많은 핵심 고객입니다.", _rule([_profile("purchase_count", "gte", 5)]), ("kakao", "email"), "loyalty_reward", ("frequency", "vip")),
    _template("high_margin_buyer", "고마진 상품 구매 고객", "loyalty", "고마진 상품 구매 이력이 있는 고객입니다.", _rule([_profile("high_margin_purchase_count", "gte", 1)]), ("email", "onsite"), "premium_recommendation", ("margin", "vip")),
    _template("referral_candidate", "추천인 캠페인 후보", "loyalty", "만족도와 구매 빈도가 높아 추천인 캠페인에 적합한 고객입니다.", _rule([_profile("purchase_count", "gte", 3), _profile("return_rate", "lte", 0.05)]), ("kakao", "email"), "referral", ("referral", "loyalty")),
    _template("coupon_lovers", "쿠폰 선호 고객", "value", "쿠폰에 반복 반응한 고객입니다.", _rule([_profile("coupon_usage_rate", "gte", 0.5)]), ("kakao", "email"), "coupon_offer", ("coupon", "value")),
    _template("discount_lovers", "할인 선호 고객", "value", "할인 상품 조회/구매 비중이 높은 고객입니다.", _rule([_profile("discount_affinity", "gte", 0.6)]), ("email", "onsite"), "discount_offer", ("discount", "value")),
    _template("free_shipping_lovers", "무료배송 선호 고객", "value", "무료배송 조건에 민감한 고객입니다.", _rule([_profile("free_shipping_affinity", "gte", 0.6)]), ("onsite", "email"), "free_shipping_offer", ("free-shipping", "value")),
    _template("premium_lovers", "프리미엄 선호 고객", "value", "가격보다 품질/프리미엄 상품에 반응하는 고객입니다.", _rule([_profile("premium_affinity", "gte", 0.6)]), ("email", "onsite"), "premium_offer", ("premium", "value")),
    _template("purchase_score_high", "구매가능성 상위 고객", "prediction", "구매 가능성이 높은 고객입니다.", _rule([_score("purchase_score", "gte", 0.75)]), ("onsite", "kakao"), "high_purchase_score", ("purchase-score", "ml")),
    _template("purchase_score_medium", "구매가능성 중간 고객", "prediction", "적절한 유도로 전환 가능성이 있는 고객입니다.", _rule([_score("purchase_score", "gte", 0.4), _score("purchase_score", "lt", 0.75)]), ("onsite", "email"), "purchase_score_nurture", ("purchase-score", "ml")),
    _template("affinity_category_high", "카테고리 선호도 상위 고객", "prediction", "특정 카테고리 반응 점수가 높은 고객입니다.", _rule([_score("category_affinity", "gte", 0.7)]), ("onsite", "email"), "category_affinity", ("affinity", "category")),
    _template("affinity_product_high", "상품 선호도 상위 고객", "prediction", "특정 상품 반응 점수가 높은 고객입니다.", _rule([_score("product_affinity", "gte", 0.7)]), ("onsite", "kakao"), "product_affinity", ("affinity", "product")),
    _template("next_best_offer_ready", "Next Best Offer 대상", "prediction", "추천 offer 반응 가능성이 높은 고객입니다.", _rule([_score("next_best_offer", "gte", 0.65)]), ("onsite", "email"), "next_best_offer", ("nbo", "ml")),
    _template("onsite_popup_candidate", "온사이트 팝업 후보", "channel", "방문 중 팝업 노출에 적합한 고객입니다.", _rule([_event("view", 1), _profile("onsite_frequency_available", "eq", True)]), ("onsite",), "onsite_decision", ("onsite", "frequency-cap")),
    _template("kakao_message_candidate", "카카오 메시지 후보", "channel", "카카오 메시지 수신 가능성이 있는 고객입니다.", _rule([_profile("kakao_opt_in", "eq", True), _profile("phone_verified", "eq", True)]), ("kakao",), "kakao_message", ("kakao", "opt-in")),
    _template("email_message_candidate", "이메일 메시지 후보", "channel", "이메일 수신 동의가 있는 고객입니다.", _rule([_profile("email_opt_in", "eq", True), _profile("email_verified", "eq", True)]), ("email",), "email_message", ("email", "opt-in")),
    _template("sms_message_candidate", "SMS 메시지 후보", "channel", "SMS 수신 동의가 있는 고객입니다.", _rule([_profile("sms_opt_in", "eq", True), _profile("phone_verified", "eq", True)]), ("sms",), "sms_message", ("sms", "opt-in")),
    _template("holdout_eligible", "홀드아웃 실험 가능 고객", "experiment", "캠페인 성과 측정을 위해 holdout 배정 가능한 고객입니다.", _rule([_profile("recent_campaign_exposure_days", "gte", 14)]), ("kakao", "email", "onsite"), "experiment_holdout", ("experiment", "holdout")),
)


class ListAudienceTemplates:
    def execute(
        self,
        category: str | None = None,
        query: str | None = None,
    ) -> tuple[AudienceTemplate, ...]:
        templates = AUDIENCE_TEMPLATES
        if category:
            templates = tuple(t for t in templates if t.category == category)
        if query:
            normalized = query.casefold()
            templates = tuple(
                t
                for t in templates
                if normalized in t.name.casefold()
                or normalized in t.description.casefold()
                or any(normalized in tag.casefold() for tag in t.tags)
            )
        return templates


class GetAudienceTemplate:
    def execute(self, template_id: str) -> AudienceTemplate | None:
        for template in AUDIENCE_TEMPLATES:
            if template.template_id == template_id:
                return template
        return None
