#!/usr/bin/env python3
"""Send realistic virtual travel events through the production collect API.

This script simulates a customer integration that already calls /v1/collect.
It does not write directly to ClickHouse; events still flow through FastAPI,
Kafka/Redpanda, and ClickHouse materialized views.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CATEGORY_PRICE_RANGES = {
    "hotel": (90000, 420000),
    "flight": (65000, 950000),
    "package": (320000, 1800000),
    "activity": (25000, 180000),
}

DEFAULT_PRODUCTS = [
    ("hotel-seoul-001", "hotel", 185000),
    ("hotel-busan-014", "hotel", 142000),
    ("hotel-jeju-021", "hotel", 238000),
    ("flight-gmp-cju", "flight", 89000),
    ("flight-icn-nrt", "flight", 310000),
    ("package-jeju-family", "package", 620000),
    ("package-osaka-weekend", "package", 790000),
    ("activity-jeju-yacht", "activity", 65000),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate virtual travel behavior events via /v1/collect."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="test-prod-key")
    parser.add_argument("--visitors", type=int, default=120)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--start-date", default="2026-06-01")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260603)
    parser.add_argument(
        "--item-count",
        type=int,
        default=len(DEFAULT_PRODUCTS),
        help="Number of virtual travel products to generate.",
    )
    parser.add_argument(
        "--target-events",
        type=int,
        default=None,
        help="Trim generated events to exactly this count.",
    )
    return parser.parse_args()


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_catalog(item_count: int, seed: int) -> list[tuple[str, str, int]]:
    if item_count <= len(DEFAULT_PRODUCTS):
        return DEFAULT_PRODUCTS[:item_count]

    random.seed(seed + 17)
    categories = tuple(CATEGORY_PRICE_RANGES)
    products = list(DEFAULT_PRODUCTS)
    for index in range(len(products) + 1, item_count + 1):
        category = categories[(index - 1) % len(categories)]
        low, high = CATEGORY_PRICE_RANGES[category]
        price = random.randrange(low, high + 1, 1000)
        products.append((f"{category}-{index:04d}", category, price))
    return products


def build_events(args: argparse.Namespace) -> list[dict]:
    random.seed(args.seed)
    products = build_catalog(args.item_count, args.seed)
    start = datetime.fromisoformat(args.start_date).replace(tzinfo=UTC)
    events: list[dict] = []

    for day_offset in range(args.days):
        day = start + timedelta(days=day_offset)
        for visitor_num in range(1, args.visitors + 1):
            visitor_id = f"virtual-visitor-{visitor_num:04d}"
            product_id, category, base_amount = random.choice(products)
            minute = random.randint(8 * 60, 23 * 60)
            occurred = day + timedelta(minutes=minute)
            event_base = f"virtual-{args.seed}-{day_offset}-{visitor_num:04d}"

            events.append({
                "event_id": f"{event_base}-view",
                "visitor_id": visitor_id,
                "type": "view",
                "product_id": product_id,
                "category": category,
                "occurred_at": iso_z(occurred),
            })

            if random.random() < 0.42:
                events.append({
                    "event_id": f"{event_base}-cart",
                    "visitor_id": visitor_id,
                    "type": "cart_add",
                    "product_id": product_id,
                    "category": category,
                    "occurred_at": iso_z(occurred + timedelta(minutes=random.randint(2, 25))),
                })

            if random.random() < 0.18:
                amount = round(base_amount * random.uniform(0.85, 1.25), -2)
                events.append({
                    "event_id": f"{event_base}-purchase",
                    "visitor_id": visitor_id,
                    "type": "purchase",
                    "product_id": product_id,
                    "category": category,
                    "amount": amount,
                    "occurred_at": iso_z(occurred + timedelta(minutes=random.randint(10, 90))),
                })

    return events


def post_batch(base_url: str, api_key: str, batch: list[dict]) -> dict:
    body = json.dumps(
        {"schema_version": "tracking-event.v1", "events": batch},
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}/v1/collect",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Sunrise-Key": api_key,
        },
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    args = parse_args()
    events = build_events(args)
    if args.target_events is not None:
        if args.target_events > len(events):
            print(
                f"target-events={args.target_events} exceeds generated={len(events)}; "
                "increase --visitors or --days",
                file=sys.stderr,
            )
            return 1
        events = events[: args.target_events]
    accepted = 0
    duplicates = 0

    try:
        for offset in range(0, len(events), args.batch_size):
            batch = events[offset : offset + args.batch_size]
            result = post_batch(args.base_url, args.api_key, batch)
            accepted += int(result["accepted"])
            duplicates += int(result["duplicates"])
            print(
                f"posted batch {offset // args.batch_size + 1}: "
                f"accepted={result['accepted']} duplicates={result['duplicates']}"
            )
    except HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "generated": len(events),
                "accepted": accepted,
                "duplicates": duplicates,
                "base_url": args.base_url,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
