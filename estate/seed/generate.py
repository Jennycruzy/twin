"""Generate the demo estate's raw source data.

Everything here is synthetic, but the shape is not arbitrary. Twin scores fragility partly
by real query volume and real row counts, so a uniform estate would produce a uniform
ranking and tell us nothing. The distributions below are the ones that make a data
platform behave like a data platform:

  * Product popularity is Zipf-like. A few hundred SKUs carry most of the orders.
  * Order volume has weekly seasonality and a growth trend across the year.
  * Payment decline rates differ by processor, so downstream fraud features have signal.
  * Roughly a third of customers never place an order.
  * Events arrive late and out of order, and a small share of sessions never terminate.

Determinism is a hard requirement: Twin's scoring output must be byte-identical across
runs, so every random draw comes from a single seeded generator and every table is written
in a fixed order. Re-running this script against the same seed produces the same estate,
row for row.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import os
import sys
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

import psycopg

# The estate is anchored to a fixed date rather than "today" so that a run in six months
# produces the same data as a run now.
ESTATE_END = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
ESTATE_DAYS = 365
ESTATE_START = ESTATE_END - dt.timedelta(days=ESTATE_DAYS)

SCHEMA_SQL = Path(__file__).with_name("schema.sql")

# --------------------------------------------------------------------------- volumes

N_CUSTOMERS = 20_000
N_CATEGORIES = 40
N_MERCHANTS = 400
N_PRODUCTS = 3_000
N_ORDERS = 120_000
N_CARRIERS = 8
N_PROMOTIONS = 120
N_SUBSCRIPTIONS = 9_000
N_SUPPORT_TICKETS = 14_000

N_PAGE_VIEWS = 400_000
N_PRODUCT_VIEWS = 180_000
N_ADD_TO_CART = 60_000
N_CHECKOUT_STEPS = 90_000
N_APP_SESSIONS = 140_000
N_DEVICES = 45_000
N_SEARCH_QUERIES = 70_000
N_EMAIL_EVENTS = 95_000
N_AUTH_EVENTS = 110_000

CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "SEK", "PLN", "MXN", "BRL"]
COUNTRIES = ["US", "GB", "DE", "FR", "CA", "AU", "JP", "SE", "PL", "MX", "BR", "NL", "ES", "IT"]
COUNTRY_CURRENCY = {
    "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "CA": "CAD", "AU": "AUD",
    "JP": "JPY", "SE": "SEK", "PL": "PLN", "MX": "MXN", "BR": "BRL", "NL": "EUR",
    "ES": "EUR", "IT": "EUR",
}

# Decline rate per processor. The spread is what gives the fraud features something to
# learn from, and it is why mart_transaction_enriched is worth building.
PROCESSORS = {"stripe": 0.061, "adyen": 0.048, "braintree": 0.079, "worldpay": 0.112}

DECLINE_REASONS = [
    "insufficient_funds", "do_not_honor", "expired_card", "suspected_fraud",
    "invalid_cvc", "issuer_unavailable",
]
ORDER_CHANNELS = ["web", "ios", "android", "partner_api"]
DEVICE_TYPES = ["desktop", "mobile", "tablet"]
OS_FAMILIES = ["iOS", "Android", "Windows", "macOS", "Linux"]
RISK_BANDS = ["low", "low", "low", "medium", "medium", "high"]
PLAN_CODES = ["starter", "growth", "scale", "enterprise"]


class Rng:
    """A seeded random generator with the few distributions this script needs.

    Wrapping ``random.Random`` rather than using it directly keeps every draw in one place,
    which is what makes the determinism guarantee auditable: there is exactly one source of
    randomness in the estate.
    """

    def __init__(self, seed: int) -> None:
        import random

        self._r = random.Random(seed)

    def rand(self) -> float:
        return self._r.random()

    def integer(self, low: int, high: int) -> int:
        """Inclusive on both ends."""
        return self._r.randint(low, high)

    def choice(self, seq: Sequence):
        return self._r.choice(seq)

    def weighted(self, options: Sequence, weights: Sequence[float]):
        return self._r.choices(options, weights=weights, k=1)[0]

    def chance(self, p: float) -> bool:
        return self._r.random() < p

    def normal(self, mu: float, sigma: float) -> float:
        return self._r.gauss(mu, sigma)

    def zipf_index(self, n: int, exponent: float = 1.1) -> int:
        """Draw an index in [0, n) with Zipf-like weighting toward low indices.

        Used for product popularity and merchant volume. Inverse-transform on a truncated
        power law: cheap, and close enough to the real long tail for our purpose.
        """
        u = self._r.random()
        return min(n - 1, int(n * (u ** exponent)))

    def timestamp(self, start: dt.datetime, end: dt.datetime) -> dt.datetime:
        span = (end - start).total_seconds()
        return start + dt.timedelta(seconds=self._r.random() * span)


def seasonal_order_timestamp(rng: Rng) -> dt.datetime:
    """Draw an order timestamp with weekly seasonality and a year-long growth trend.

    Rejection sampling against a demand curve: weekends run about 40% below weekdays, and
    the back half of the year carries roughly 1.6x the front half.
    """
    while True:
        ts = rng.timestamp(ESTATE_START, ESTATE_END)
        day_progress = (ts - ESTATE_START).days / ESTATE_DAYS
        trend = 0.75 + 0.85 * day_progress
        weekday_factor = 0.6 if ts.weekday() >= 5 else 1.0
        # Business-hours bias, softened so nights are quiet rather than empty.
        hour_factor = 0.45 + 0.55 * math.sin(math.pi * min(max(ts.hour - 5, 0), 18) / 18)
        if rng.rand() < trend * weekday_factor * hour_factor / 1.6:
            return ts


def stable_id(*parts: object) -> str:
    """A short deterministic identifier derived from its inputs.

    Event ids must be stable across runs and unique across ~1M rows, and must not depend on
    insertion order. A truncated blake2b of the natural key gives both.
    """
    digest = hashlib.blake2b(
        "|".join(str(p) for p in parts).encode("utf-8"), digest_size=8
    ).hexdigest()
    return digest


# --------------------------------------------------------------------------- raw_pg

def gen_customers(rng: Rng) -> list[tuple]:
    rows = []
    for cid in range(1, N_CUSTOMERS + 1):
        country = rng.choice(COUNTRIES)
        signup = rng.timestamp(ESTATE_START - dt.timedelta(days=540), ESTATE_END)
        # A realistic estate has churned and soft-deleted rows in it.
        status = rng.weighted(
            ["active", "dormant", "closed"], [0.72, 0.21, 0.07]
        )
        deleted = None
        if status == "closed" and rng.chance(0.4):
            deleted = rng.timestamp(signup, ESTATE_END)
        tier = rng.weighted(
            [None, "bronze", "silver", "gold", "platinum"], [0.34, 0.30, 0.21, 0.11, 0.04]
        )
        rows.append((
            cid,
            f"customer{cid}@example-{country.lower()}.test",
            f"Customer {cid}",
            country,
            signup,
            status,
            rng.chance(0.58),
            tier,
            deleted,
        ))
    return rows


def gen_addresses(rng: Rng, customers: list[tuple]) -> Iterator[tuple]:
    address_id = 0
    for cust in customers:
        cid, country = cust[0], cust[3]
        for idx in range(rng.weighted([1, 2, 3], [0.68, 0.27, 0.05])):
            address_id += 1
            yield (
                address_id,
                cid,
                "shipping" if idx == 0 else rng.choice(["shipping", "billing"]),
                f"{rng.integer(1, 998)} Example Street",
                f"City {rng.integer(1, 400)}",
                f"Region {rng.integer(1, 40)}",
                f"{rng.integer(10000, 99999)}",
                country,
                idx == 0,
            )


def gen_categories(rng: Rng) -> list[tuple]:
    rows = []
    for cat_id in range(1, N_CATEGORIES + 1):
        # The first eight categories are roots; the rest hang off them.
        parent = None if cat_id <= 8 else rng.integer(1, 8)
        rows.append((cat_id, f"Category {cat_id}", parent, rng.chance(0.15)))
    return rows


def gen_merchants(rng: Rng) -> list[tuple]:
    rows = []
    for mid in range(1, N_MERCHANTS + 1):
        country = rng.choice(COUNTRIES)
        rows.append((
            mid,
            f"Merchant {mid}",
            country,
            rng.timestamp(ESTATE_START - dt.timedelta(days=900), ESTATE_END),
            rng.choice(RISK_BANDS),
            COUNTRY_CURRENCY[country],
            rng.chance(0.03),
        ))
    return rows


def gen_products(rng: Rng) -> list[tuple]:
    rows = []
    for pid in range(1, N_PRODUCTS + 1):
        merchant_id = 1 + rng.zipf_index(N_MERCHANTS)
        country = COUNTRIES[merchant_id % len(COUNTRIES)]
        price = round(max(1.5, rng.normal(48, 62)), 2)
        rows.append((
            pid,
            merchant_id,
            rng.integer(1, N_CATEGORIES),
            f"SKU-{pid:06d}",
            f"Product {pid}",
            price,
            COUNTRY_CURRENCY[country],
            rng.chance(0.88),
            rng.timestamp(ESTATE_START - dt.timedelta(days=700), ESTATE_END),
        ))
    return rows


def gen_orders(rng: Rng, customers: list[tuple], products: list[tuple]) -> list[tuple]:
    """Orders, held in memory because payments, shipments and items all reference them.

    Only about two thirds of customers ever order, and ordering customers are drawn with
    Zipf weighting, so a small cohort accounts for a large share of volume.
    """
    ordering_pool = [c[0] for c in customers if rng.chance(0.66)]
    rows = []
    for order_id in range(1, N_ORDERS + 1):
        customer_id = ordering_pool[rng.zipf_index(len(ordering_pool), exponent=1.35)]
        product = products[rng.zipf_index(len(products), exponent=1.25)]
        merchant_id, currency = product[1], product[6]
        ts = seasonal_order_timestamp(rng)
        gross = round(max(3.0, rng.normal(94, 110)), 2)
        discount = round(gross * rng.weighted([0.0, 0.05, 0.1, 0.2], [0.7, 0.13, 0.11, 0.06]), 2)
        status = rng.weighted(
            ["completed", "cancelled", "pending", "returned"], [0.86, 0.06, 0.03, 0.05]
        )
        rows.append((
            order_id, customer_id, merchant_id, ts, status, currency,
            gross, discount, round(rng.weighted([0.0, 4.99, 9.99], [0.4, 0.4, 0.2]), 2),
            rng.weighted(ORDER_CHANNELS, [0.44, 0.27, 0.24, 0.05]),
        ))
    return rows


def gen_order_items(rng: Rng, orders: list[tuple], products: list[tuple]) -> Iterator[tuple]:
    item_id = 0
    for order in orders:
        order_id, currency = order[0], order[5]
        for _ in range(rng.weighted([1, 2, 3, 4, 5], [0.52, 0.24, 0.13, 0.07, 0.04])):
            item_id += 1
            product = products[rng.zipf_index(len(products), exponent=1.25)]
            yield (
                item_id, order_id, product[0],
                rng.weighted([1, 2, 3], [0.79, 0.16, 0.05]),
                product[5], currency,
            )


def gen_payment_methods(rng: Rng, orders: list[tuple]) -> tuple[list[tuple], dict[int, list[int]]]:
    """One to three stored methods per customer that has ever ordered."""
    by_customer: dict[int, list[int]] = {}
    rows: list[tuple] = []
    method_id = 0
    for customer_id in sorted({o[1] for o in orders}):
        methods = []
        for _ in range(rng.weighted([1, 2, 3], [0.71, 0.23, 0.06])):
            method_id += 1
            method_type = rng.weighted(
                ["card", "wallet", "bank_debit"], [0.78, 0.16, 0.06]
            )
            rows.append((
                method_id, customer_id, method_type,
                f"{rng.integer(400000, 599999)}" if method_type == "card" else None,
                rng.choice(COUNTRIES) if method_type == "card" else None,
                rng.timestamp(ESTATE_START - dt.timedelta(days=400), ESTATE_END),
            ))
            methods.append(method_id)
        by_customer[customer_id] = methods
    return rows, by_customer


def gen_payments(
    rng: Rng, orders: list[tuple], methods_by_customer: dict[int, list[int]]
) -> list[tuple]:
    rows = []
    payment_id = 0
    for order in orders:
        order_id, customer_id, _, order_ts, status, currency, gross, discount, shipping, _ = order
        if status == "pending":
            continue
        amount = round(gross - discount + shipping, 2)
        processor = rng.weighted(list(PROCESSORS), [0.42, 0.28, 0.19, 0.11])
        declined = rng.chance(PROCESSORS[processor])
        payment_id += 1
        attempted = order_ts + dt.timedelta(seconds=rng.integer(2, 240))
        rows.append((
            payment_id, order_id, rng.choice(methods_by_customer[customer_id]),
            attempted,
            None if declined else attempted + dt.timedelta(seconds=rng.integer(1, 90)),
            "declined" if declined else "settled",
            amount, currency, processor,
            rng.choice(DECLINE_REASONS) if declined else None,
        ))
        # A declined attempt is usually retried, sometimes successfully.
        if declined and rng.chance(0.62):
            payment_id += 1
            retry_ts = attempted + dt.timedelta(minutes=rng.integer(1, 180))
            retry_declined = rng.chance(0.31)
            rows.append((
                payment_id, order_id, rng.choice(methods_by_customer[customer_id]),
                retry_ts,
                None if retry_declined else retry_ts + dt.timedelta(seconds=rng.integer(1, 90)),
                "declined" if retry_declined else "settled",
                amount, currency, processor,
                rng.choice(DECLINE_REASONS) if retry_declined else None,
            ))
    return rows


def gen_refunds(rng: Rng, payments: list[tuple]) -> Iterator[tuple]:
    refund_id = 0
    for payment in payments:
        if payment[5] != "settled" or not rng.chance(0.052):
            continue
        refund_id += 1
        settled_ts = payment[4]
        # Partial refunds are common and matter to finance reconciliation.
        share = rng.weighted([1.0, 0.5, 0.25], [0.63, 0.24, 0.13])
        yield (
            refund_id, payment[0],
            settled_ts + dt.timedelta(days=rng.integer(1, 45)),
            round(float(payment[6]) * share, 2), payment[7],
            rng.choice(["customer_request", "item_damaged", "not_delivered", "duplicate"]),
        )


def gen_chargebacks(rng: Rng, payments: list[tuple]) -> Iterator[tuple]:
    chargeback_id = 0
    for payment in payments:
        if payment[5] != "settled" or not rng.chance(0.0071):
            continue
        chargeback_id += 1
        opened = payment[4] + dt.timedelta(days=rng.integer(5, 90))
        resolved = opened + dt.timedelta(days=rng.integer(10, 60)) if rng.chance(0.7) else None
        yield (
            chargeback_id, payment[0], opened, resolved,
            payment[6], payment[7],
            rng.choice(["fraud", "product_not_received", "unrecognised", "subscription_cancelled"]),
            rng.weighted(["merchant", "issuer", "platform"], [0.61, 0.28, 0.11]),
        )


def gen_carriers(rng: Rng) -> list[tuple]:
    levels = ["economy", "standard", "express", "same_day"]
    return [
        (i, f"Carrier {i}", levels[(i - 1) % len(levels)], [7, 4, 2, 1][(i - 1) % 4])
        for i in range(1, N_CARRIERS + 1)
    ]


def gen_shipments(rng: Rng, orders: list[tuple], carriers: list[tuple]) -> Iterator[tuple]:
    shipment_id = 0
    for order in orders:
        if order[4] in ("cancelled", "pending"):
            continue
        shipment_id += 1
        carrier = rng.choice(carriers)
        order_ts = order[3]
        shipped = order_ts + dt.timedelta(hours=rng.integer(4, 96)) if rng.chance(0.97) else None
        delivered = None
        status = "pending"
        if shipped:
            # Late delivery is what makes mart_logistics_sla non-trivial.
            transit_days = carrier[3] + rng.weighted([0, 1, 2, 5], [0.68, 0.19, 0.09, 0.04])
            if rng.chance(0.94):
                delivered = shipped + dt.timedelta(days=transit_days)
                status = "delivered"
            else:
                status = "in_transit"
        yield (
            shipment_id, order[0], carrier[0], shipped, delivered, status,
            stable_id("ship", shipment_id) if shipped else None,
        )


def gen_promotions(rng: Rng) -> list[tuple]:
    rows = []
    for pid in range(1, N_PROMOTIONS + 1):
        starts = rng.timestamp(ESTATE_START, ESTATE_END - dt.timedelta(days=7))
        discount_type = rng.weighted(["percent", "fixed"], [0.7, 0.3])
        rows.append((
            pid, f"PROMO{pid:04d}", discount_type,
            round(rng.integer(5, 30) if discount_type == "percent" else rng.integer(5, 50), 2),
            starts, starts + dt.timedelta(days=rng.integer(3, 60)),
            round(rng.integer(2_000, 90_000), 2),
        ))
    return rows


def gen_redemptions(rng: Rng, orders: list[tuple], promotions: list[tuple]) -> Iterator[tuple]:
    redemption_id = 0
    for order in orders:
        discount = float(order[7])
        if discount <= 0:
            continue
        redemption_id += 1
        yield (
            redemption_id, rng.choice(promotions)[0], order[0], order[1],
            order[3], round(discount, 2),
        )


def gen_subscriptions(rng: Rng, customers: list[tuple]) -> Iterator[tuple]:
    pool = [c for c in customers if rng.chance(0.45)]
    for sub_id in range(1, N_SUBSCRIPTIONS + 1):
        cust = pool[rng.zipf_index(len(pool))]
        started = rng.timestamp(ESTATE_START - dt.timedelta(days=300), ESTATE_END)
        cancelled = None
        if rng.chance(0.27):
            cancelled = started + dt.timedelta(days=rng.integer(20, 400))
            if cancelled > ESTATE_END:
                cancelled = None
        interval = rng.weighted(["monthly", "annual"], [0.82, 0.18])
        plan = rng.weighted(PLAN_CODES, [0.46, 0.31, 0.17, 0.06])
        mrr = {"starter": 19, "growth": 79, "scale": 249, "enterprise": 900}[plan]
        yield (
            sub_id, cust[0], plan, started, cancelled,
            round(mrr * (12 if interval == "annual" else 1) * rng.normal(1.0, 0.04), 2),
            COUNTRY_CURRENCY[cust[3]], interval,
        )


def gen_support_tickets(rng: Rng, orders: list[tuple], customers: list[tuple]) -> Iterator[tuple]:
    for ticket_id in range(1, N_SUPPORT_TICKETS + 1):
        linked = rng.chance(0.71)
        order = orders[rng.integer(0, len(orders) - 1)] if linked else None
        opened = order[3] + dt.timedelta(days=rng.integer(0, 20)) if order else rng.timestamp(
            ESTATE_START, ESTATE_END
        )
        closed = opened + dt.timedelta(hours=rng.integer(1, 400)) if rng.chance(0.86) else None
        yield (
            ticket_id,
            order[1] if order else rng.choice(customers)[0],
            order[0] if order else None,
            opened, closed,
            rng.choice(["delivery", "payment", "product", "account", "refund"]),
            rng.weighted(["low", "medium", "high", "urgent"], [0.38, 0.36, 0.19, 0.07]),
            rng.integer(1, 5) if closed and rng.chance(0.55) else None,
        )


def gen_fx_rates(rng: Rng) -> Iterator[tuple]:
    """Daily USD-base rates for every non-USD currency in the estate.

    A random walk rather than independent draws per day, because a rate series that jumps
    randomly day to day would make every downstream revenue figure nonsense.
    """
    base_levels = {
        "EUR": 0.92, "GBP": 0.79, "CAD": 1.36, "AUD": 1.52, "JPY": 151.0,
        "SEK": 10.6, "PLN": 3.98, "MXN": 17.1, "BRL": 5.05,
    }
    levels = dict(base_levels)
    for day_offset in range(ESTATE_DAYS + 1):
        rate_date = (ESTATE_START + dt.timedelta(days=day_offset)).date()
        for quote in sorted(base_levels):
            levels[quote] *= 1.0 + rng.normal(0.0, 0.004)
            yield (rate_date, "USD", quote, round(levels[quote], 8), "ecb_daily")


# --------------------------------------------------------------------------- raw_events

def gen_devices(rng: Rng) -> list[tuple]:
    rows = []
    for i in range(N_DEVICES):
        device_id = stable_id("device", i)
        first_seen = rng.timestamp(ESTATE_START, ESTATE_END - dt.timedelta(days=1))
        last_seen = first_seen + dt.timedelta(days=rng.integer(0, 300))
        rows.append((
            device_id,
            int(first_seen.timestamp() * 1000),
            int(min(last_seen, ESTATE_END).timestamp() * 1000),
            rng.choice(OS_FAMILIES),
            rng.chance(0.021),
            round(min(1.0, max(0.0, rng.normal(0.78, 0.19))), 4),
        ))
    return rows


def gen_app_sessions(
    rng: Rng, customers: list[tuple], devices: list[tuple]
) -> list[tuple]:
    rows = []
    for i in range(N_APP_SESSIONS):
        session_id = stable_id("session", i)
        started = rng.timestamp(ESTATE_START, ESTATE_END)
        # Anonymous sessions are a large minority and must survive the joins downstream.
        customer_id = customers[rng.zipf_index(len(customers), 1.3)][0] if rng.chance(0.63) else None
        # ~4% of sessions never emit a terminating event. Real streams do this.
        ended = None
        if rng.chance(0.96):
            ended = started + dt.timedelta(seconds=rng.integer(8, 5400))
        rows.append((
            session_id, customer_id, rng.choice(devices)[0],
            int(started.timestamp() * 1000),
            int(ended.timestamp() * 1000) if ended else None,
            f"{rng.integer(3, 8)}.{rng.integer(0, 20)}.{rng.integer(0, 9)}",
            rng.choice(COUNTRIES) if rng.chance(0.93) else None,
        ))
    return rows


def _event_ts_ms(rng: Rng, session_started_ms: int) -> int:
    """An event timestamp inside a session, with occasional late arrivals.

    About 2% of events land with a negative offset relative to their session start, which
    is what out-of-order stream delivery looks like once it reaches the warehouse.
    """
    offset = rng.integer(0, 5_400_000)
    if rng.chance(0.02):
        offset = -rng.integer(1, 120_000)
    return session_started_ms + offset


def gen_page_views(rng: Rng, sessions: list[tuple]) -> Iterator[tuple]:
    paths = ["/", "/search", "/product", "/cart", "/checkout", "/account", "/orders", "/help"]
    sources = [None, "google", "meta", "email", "affiliate", "direct"]
    for i in range(N_PAGE_VIEWS):
        session = sessions[rng.integer(0, len(sessions) - 1)]
        yield (
            stable_id("pv", i), session[0], session[1],
            _event_ts_ms(rng, session[3]),
            rng.weighted(paths, [0.28, 0.16, 0.22, 0.09, 0.06, 0.07, 0.06, 0.06]),
            rng.choice(sources), rng.choice(sources), rng.choice(DEVICE_TYPES),
        )


def gen_product_views(rng: Rng, sessions: list[tuple], products: list[tuple]) -> Iterator[tuple]:
    for i in range(N_PRODUCT_VIEWS):
        session = sessions[rng.integer(0, len(sessions) - 1)]
        yield (
            stable_id("prv", i), session[0], session[1],
            products[rng.zipf_index(len(products), 1.25)][0],
            _event_ts_ms(rng, session[3]),
            rng.integer(400, 240_000) if rng.chance(0.91) else None,
        )


def gen_add_to_cart(rng: Rng, sessions: list[tuple], products: list[tuple]) -> Iterator[tuple]:
    for i in range(N_ADD_TO_CART):
        session = sessions[rng.integer(0, len(sessions) - 1)]
        yield (
            stable_id("atc", i), session[0], session[1],
            products[rng.zipf_index(len(products), 1.25)][0],
            rng.weighted([1, 2, 3], [0.81, 0.14, 0.05]),
            _event_ts_ms(rng, session[3]),
        )


def gen_checkout_steps(rng: Rng, sessions: list[tuple]) -> Iterator[tuple]:
    steps = ["cart_review", "shipping", "payment", "confirm"]
    for i in range(N_CHECKOUT_STEPS):
        session = sessions[rng.integer(0, len(sessions) - 1)]
        step_index = rng.weighted([0, 1, 2, 3], [0.38, 0.27, 0.21, 0.14])
        yield (
            stable_id("chk", i), session[0], session[1],
            steps[step_index], step_index,
            _event_ts_ms(rng, session[3]),
            # Abandonment concentrates in the later steps.
            rng.chance(0.11 + 0.13 * step_index),
        )


def gen_search_queries(rng: Rng, sessions: list[tuple]) -> Iterator[tuple]:
    for i in range(N_SEARCH_QUERIES):
        session = sessions[rng.integer(0, len(sessions) - 1)]
        result_count = 0 if rng.chance(0.09) else rng.integer(1, 480)
        yield (
            stable_id("sq", i), session[0], session[1],
            f"query term {rng.zipf_index(2_500)}",
            result_count, _event_ts_ms(rng, session[3]),
        )


def gen_email_events(rng: Rng, customers: list[tuple]) -> Iterator[tuple]:
    types = ["sent", "delivered", "opened", "clicked", "bounced", "unsubscribed"]
    for i in range(N_EMAIL_EVENTS):
        cust = customers[rng.zipf_index(len(customers), 1.2)]
        yield (
            stable_id("em", i), cust[0], f"campaign-{rng.integer(1, 90)}",
            rng.weighted(types, [0.34, 0.30, 0.20, 0.09, 0.05, 0.02]),
            int(rng.timestamp(ESTATE_START, ESTATE_END).timestamp() * 1000),
        )


def gen_auth_events(rng: Rng, customers: list[tuple], devices: list[tuple]) -> Iterator[tuple]:
    types = ["login", "logout", "password_reset", "mfa_challenge", "token_refresh"]
    for i in range(N_AUTH_EVENTS):
        event_type = rng.weighted(types, [0.44, 0.21, 0.06, 0.12, 0.17])
        # Failed logins cluster, and are the signal feature_customer_risk is built on.
        succeeded = rng.chance(0.91 if event_type != "mfa_challenge" else 0.84)
        yield (
            stable_id("auth", i),
            customers[rng.zipf_index(len(customers), 1.3)][0] if rng.chance(0.94) else None,
            rng.choice(devices)[0], event_type, succeeded,
            rng.choice(COUNTRIES) if rng.chance(0.96) else None,
            int(rng.timestamp(ESTATE_START, ESTATE_END).timestamp() * 1000),
        )


# --------------------------------------------------------------------------- loading

def copy_rows(conn: psycopg.Connection, table: str, columns: Sequence[str], rows: Iterable[tuple]) -> int:
    """Stream rows into a table with COPY, returning the count written.

    COPY rather than executemany because the estate is ~1.9M rows and the difference is
    minutes, and `make estate` has a runtime budget.
    """
    written = 0
    column_list = ", ".join(columns)
    with conn.cursor().copy(f"COPY {table} ({column_list}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            written += 1
    return written


TableSpec = tuple[str, Sequence[str], Callable[[], Iterable[tuple]]]


def build_plan(rng: Rng) -> tuple[list[TableSpec], dict]:
    """Materialise the entities that later tables reference, then describe every load.

    Returns the ordered load plan. Order matters: it satisfies foreign keys, and it keeps
    the run deterministic because every generator draws from the same sequence.
    """
    customers = gen_customers(rng)
    categories = gen_categories(rng)
    merchants = gen_merchants(rng)
    products = gen_products(rng)
    orders = gen_orders(rng, customers, products)
    carriers = gen_carriers(rng)
    promotions = gen_promotions(rng)
    payment_methods, methods_by_customer = gen_payment_methods(rng, orders)
    payments = gen_payments(rng, orders, methods_by_customer)
    devices = gen_devices(rng)
    sessions = gen_app_sessions(rng, customers, devices)

    plan: list[TableSpec] = [
        ("raw_pg.customers",
         ["customer_id", "email", "full_name", "country_code", "signup_ts",
          "account_status", "marketing_opt_in", "lifetime_tier", "deleted_at"],
         lambda: customers),
        ("raw_pg.addresses",
         ["address_id", "customer_id", "address_type", "line1", "city", "region",
          "postal_code", "country_code", "is_default"],
         lambda: gen_addresses(rng, customers)),
        ("raw_pg.product_categories",
         ["category_id", "category_name", "parent_category_id", "is_regulated"],
         lambda: categories),
        ("raw_pg.merchants",
         ["merchant_id", "merchant_name", "country_code", "onboarded_ts", "risk_band",
          "settlement_currency", "is_suspended"],
         lambda: merchants),
        ("raw_pg.products",
         ["product_id", "merchant_id", "category_id", "sku", "title", "list_price",
          "currency_code", "is_active", "created_ts"],
         lambda: products),
        ("raw_pg.orders",
         ["order_id", "customer_id", "merchant_id", "order_ts", "status", "currency_code",
          "gross_amount", "discount_amount", "shipping_amount", "channel"],
         lambda: orders),
        ("raw_pg.order_items",
         ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "currency_code"],
         lambda: gen_order_items(rng, orders, products)),
        ("raw_pg.payment_methods",
         ["payment_method_id", "customer_id", "method_type", "card_bin", "issuer_country", "added_ts"],
         lambda: payment_methods),
        ("raw_pg.payments",
         ["payment_id", "order_id", "payment_method_id", "attempted_ts", "settled_ts",
          "status", "amount", "currency_code", "processor", "decline_reason"],
         lambda: payments),
        ("raw_pg.refunds",
         ["refund_id", "payment_id", "refunded_ts", "amount", "currency_code", "reason_code"],
         lambda: gen_refunds(rng, payments)),
        ("raw_pg.chargebacks",
         ["chargeback_id", "payment_id", "opened_ts", "resolved_ts", "amount",
          "currency_code", "reason_code", "liability"],
         lambda: gen_chargebacks(rng, payments)),
        ("raw_pg.carriers",
         ["carrier_id", "carrier_name", "service_level", "promised_days"],
         lambda: carriers),
        ("raw_pg.shipments",
         ["shipment_id", "order_id", "carrier_id", "shipped_ts", "delivered_ts", "status", "tracking_ref"],
         lambda: gen_shipments(rng, orders, carriers)),
        ("raw_pg.promotions",
         ["promotion_id", "promo_code", "discount_type", "discount_value", "starts_ts",
          "ends_ts", "budget_amount"],
         lambda: promotions),
        ("raw_pg.promotion_redemptions",
         ["redemption_id", "promotion_id", "order_id", "customer_id", "redeemed_ts", "discount_applied"],
         lambda: gen_redemptions(rng, orders, promotions)),
        ("raw_pg.subscriptions",
         ["subscription_id", "customer_id", "plan_code", "started_ts", "cancelled_ts",
          "mrr_amount", "currency_code", "billing_interval"],
         lambda: gen_subscriptions(rng, customers)),
        ("raw_pg.support_tickets",
         ["ticket_id", "customer_id", "order_id", "opened_ts", "closed_ts", "category",
          "priority", "csat_score"],
         lambda: gen_support_tickets(rng, orders, customers)),
        ("raw_pg.fx_rates",
         ["rate_date", "base_currency", "quote_currency", "rate", "source"],
         lambda: gen_fx_rates(rng)),

        ("raw_events.device_fingerprints",
         ["device_id", "first_seen_ms", "last_seen_ms", "os_family", "is_emulator", "trust_score"],
         lambda: devices),
        ("raw_events.app_sessions",
         ["session_id", "customer_id", "device_id", "started_ts_ms", "ended_ts_ms",
          "app_version", "ip_country"],
         lambda: sessions),
        ("raw_events.page_views",
         ["event_id", "session_id", "customer_id", "event_ts_ms", "path", "referrer",
          "utm_source", "device_type"],
         lambda: gen_page_views(rng, sessions)),
        ("raw_events.product_views",
         ["event_id", "session_id", "customer_id", "product_id", "event_ts_ms", "dwell_ms"],
         lambda: gen_product_views(rng, sessions, products)),
        ("raw_events.add_to_cart",
         ["event_id", "session_id", "customer_id", "product_id", "quantity", "event_ts_ms"],
         lambda: gen_add_to_cart(rng, sessions, products)),
        ("raw_events.checkout_steps",
         ["event_id", "session_id", "customer_id", "step_name", "step_index", "event_ts_ms", "abandoned"],
         lambda: gen_checkout_steps(rng, sessions)),
        ("raw_events.search_queries",
         ["event_id", "session_id", "customer_id", "query_text", "result_count", "event_ts_ms"],
         lambda: gen_search_queries(rng, sessions)),
        ("raw_events.email_events",
         ["event_id", "customer_id", "campaign_id", "event_type", "event_ts_ms"],
         lambda: gen_email_events(rng, customers)),
        ("raw_events.auth_events",
         ["event_id", "customer_id", "device_id", "event_type", "succeeded", "ip_country", "event_ts_ms"],
         lambda: gen_auth_events(rng, customers, devices)),
    ]
    return plan, {"orders": len(orders), "payments": len(payments), "sessions": len(sessions)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Twin demo estate's raw layer.")
    parser.add_argument(
        "--seed",
        type=int,
        default=int(os.environ.get("TWIN_SEED", "20260805")),
        help="RNG seed. The same seed always produces the same estate.",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Warehouse connection string. Defaults to the WAREHOUSE_* environment.",
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or (
        f"host={os.environ.get('WAREHOUSE_HOST', 'warehouse')} "
        f"port={os.environ.get('WAREHOUSE_PORT', '5432')} "
        f"dbname={os.environ.get('WAREHOUSE_DB', 'warehouse')} "
        f"user={os.environ.get('WAREHOUSE_USER', 'twin')} "
        f"password={os.environ.get('WAREHOUSE_PASSWORD', 'twin')}"
    )

    print(f"seeding estate  seed={args.seed}  window={ESTATE_START.date()}..{ESTATE_END.date()}")
    rng = Rng(args.seed)
    plan, stats = build_plan(rng)
    print(f"  generated in memory: {stats['orders']:,} orders, "
          f"{stats['payments']:,} payments, {stats['sessions']:,} sessions")

    started = dt.datetime.now()
    total = 0
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute(SCHEMA_SQL.read_text())
        for table, columns, rows in plan:
            count = copy_rows(conn, table, columns, rows())
            total += count
            print(f"  {table:<36} {count:>9,} rows")
        # Grants are re-applied because the schemas were dropped and recreated above.
        for schema in ("raw_pg", "raw_events"):
            conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO twin_reader, twin_shadow")
            conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO twin_reader, twin_shadow")
        conn.commit()

    elapsed = (dt.datetime.now() - started).total_seconds()
    print(f"seeded {total:,} rows across {len(plan)} tables in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
