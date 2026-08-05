"""Tests for the estate generator's invariants.

Determinism is the one Twin cannot compromise on. Scoring output must be byte-identical
across runs, and that guarantee starts here: if the estate differs between two runs with
the same seed, nothing downstream can be reproducible no matter how careful it is.

These tests run against the generator directly and need neither DataHub nor a warehouse,
so they stay fast enough to run on every change. The complementary check that the estate
actually landed correctly in DataHub is `make verify-estate`, which is an integration test
by nature and lives outside pytest.
"""

from __future__ import annotations

import datetime as dt

import pytest

from estate.seed.generate import (
    ESTATE_DAYS,
    ESTATE_END,
    ESTATE_START,
    Rng,
    gen_customers,
    gen_fx_rates,
    gen_merchants,
    gen_orders,
    gen_products,
    seasonal_order_timestamp,
    stable_id,
)

SEED = 20260805


def test_rng_is_reproducible_for_a_given_seed():
    a = Rng(SEED)
    b = Rng(SEED)
    assert [a.rand() for _ in range(50)] == [b.rand() for _ in range(50)]


def test_rng_diverges_for_different_seeds():
    a = Rng(SEED)
    b = Rng(SEED + 1)
    assert [a.rand() for _ in range(50)] != [b.rand() for _ in range(50)]


def test_customers_are_identical_across_runs_with_the_same_seed():
    assert gen_customers(Rng(SEED)) == gen_customers(Rng(SEED))


def test_customers_differ_when_the_seed_changes():
    """The seed must actually reach the data, not just the generator object.

    A generator that ignored its seed would pass the reproducibility test above while
    making the seed meaningless.
    """
    assert gen_customers(Rng(SEED)) != gen_customers(Rng(SEED + 1))


def test_orders_are_identical_across_runs_with_the_same_seed():
    def build(seed: int):
        rng = Rng(seed)
        customers = gen_customers(rng)
        gen_merchants(rng)
        products = gen_products(rng)
        return gen_orders(rng, customers, products)

    assert build(SEED) == build(SEED)


def test_stable_id_is_deterministic_and_content_addressed():
    assert stable_id("pv", 1) == stable_id("pv", 1)
    assert stable_id("pv", 1) != stable_id("pv", 2)
    assert stable_id("pv", 1) != stable_id("prv", 1)


def test_stable_id_does_not_collide_across_a_realistic_volume():
    """Event ids must be unique across the largest table the estate generates."""
    ids = {stable_id("pv", i) for i in range(400_000)}
    assert len(ids) == 400_000


class TestFxRates:
    """fx_rates underpins every currency conversion in the estate.

    A gap in the series, or a non-positive rate, would silently drop or corrupt revenue in
    every downstream model rather than failing loudly, so both are asserted.
    """

    @pytest.fixture(scope="class")
    def rates(self):
        return list(gen_fx_rates(Rng(SEED)))

    def test_covers_every_day_in_the_window_with_no_gaps(self, rates):
        dates = sorted({row[0] for row in rates})
        assert dates[0] == ESTATE_START.date()
        assert dates[-1] == ESTATE_END.date()
        assert len(dates) == ESTATE_DAYS + 1
        spans = {(b - a).days for a, b in zip(dates, dates[1:])}
        assert spans == {1}

    def test_every_currency_is_present_on_every_day(self, rates):
        by_date = {}
        for rate_date, _, quote, _, _ in rates:
            by_date.setdefault(rate_date, set()).add(quote)
        assert len({frozenset(v) for v in by_date.values()}) == 1

    def test_rates_are_positive(self, rates):
        assert all(row[3] > 0 for row in rates)

    def test_rates_move_as_a_walk_rather_than_independent_draws(self, rates):
        """Consecutive rates for a currency must be close.

        Independent daily draws would make every historical revenue figure in the estate
        nonsense while still passing the positivity check above.
        """
        series = [row[3] for row in rates if row[2] == "EUR"]
        moves = [abs(b - a) / a for a, b in zip(series, series[1:])]
        assert max(moves) < 0.05


class TestOrderSeasonality:
    """Order volume must carry the seasonality the estate claims.

    Twin weights fragility partly by activity, so a flat order distribution would make the
    estate's busiest and quietest assets indistinguishable.
    """

    @pytest.fixture(scope="class")
    def timestamps(self):
        rng = Rng(SEED)
        return [seasonal_order_timestamp(rng) for _ in range(20_000)]

    def test_all_orders_fall_inside_the_estate_window(self, timestamps):
        assert all(ESTATE_START <= ts <= ESTATE_END for ts in timestamps)

    def test_weekends_are_quieter_than_weekdays(self, timestamps):
        weekend = sum(1 for ts in timestamps if ts.weekday() >= 5) / 2
        weekday = sum(1 for ts in timestamps if ts.weekday() < 5) / 5
        assert weekend < weekday * 0.8

    def test_volume_grows_across_the_window(self, timestamps):
        midpoint = ESTATE_START + dt.timedelta(days=ESTATE_DAYS / 2)
        first_half = sum(1 for ts in timestamps if ts < midpoint)
        second_half = len(timestamps) - first_half
        assert second_half > first_half * 1.2


def test_orders_reference_only_real_customers_and_products():
    """Referential integrity, checked before the database is asked to enforce it.

    The raw tables carry foreign keys, so a violation would surface as a COPY failure with
    a message pointing at the load rather than at the generator that caused it.
    """
    rng = Rng(SEED)
    customers = gen_customers(rng)
    gen_merchants(rng)
    products = gen_products(rng)
    orders = gen_orders(rng, customers, products)

    customer_ids = {c[0] for c in customers}
    merchant_ids = {p[1] for p in products}
    assert {o[1] for o in orders} <= customer_ids
    assert {o[2] for o in orders} <= merchant_ids


def test_a_realistic_minority_of_customers_never_order():
    """A platform where every customer buys is not a platform anyone recognises."""
    rng = Rng(SEED)
    customers = gen_customers(rng)
    gen_merchants(rng)
    products = gen_products(rng)
    orders = gen_orders(rng, customers, products)

    ordering = {o[1] for o in orders}
    share = len(ordering) / len(customers)
    assert 0.3 < share < 0.75
