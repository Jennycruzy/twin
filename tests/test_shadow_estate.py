"""Tests for how the shadow estate is laid out and how consumer queries are re-pointed.

These cover the decisions that decide whether a verification means anything: which models
are copied, which are left to be rebuilt for real, and whether the schema a statement names
is the disposable one. The parts that need a warehouse are exercised by `make run`, which is
an integration test by nature.
"""

from __future__ import annotations

from twin.read.model import Asset, Edge, EstateGraph
from twin.simulate.scenario import Fault, Scenario
from twin.verify.consumers import repoint
from twin.verify.guard import SHADOW_PREFIX
from twin.verify.shadow import model_name, plan

import datetime as dt
from pathlib import Path


def dataset(key: str) -> Asset:
    return Asset(key=key, kind="dataset", name=key.split(".")[-1], urns=(key,), layer=key.split(".")[0])


def estate() -> EstateGraph:
    return EstateGraph(
        assets=(
            dataset("raw_pg.fx_rates"),
            dataset("staging.stg_fx_rates"),
            dataset("staging.stg_orders"),
            dataset("intermediate.int_orders_enriched"),
            dataset("marts.mart_revenue_daily"),
            dataset("marts.mart_logistics_sla"),
        ),
        edges=(
            Edge("raw_pg.fx_rates", "staging.stg_fx_rates"),
            Edge("staging.stg_fx_rates", "intermediate.int_orders_enriched"),
            Edge("staging.stg_orders", "intermediate.int_orders_enriched"),
            Edge("intermediate.int_orders_enriched", "marts.mart_revenue_daily"),
        ),
        column_edges=(),
        read_at="2026-08-05T09:00:00+00:00",
        source="test",
    )


def scenario() -> Scenario:
    return Scenario(
        name="fx_rate_column_drop",
        title="t",
        description="",
        fault=Fault(kind="drop_column", asset="staging.stg_fx_rates", column="rate", at=dt.time(4, 12)),
        path=Path("scenarios/fx_rate_column_drop.yml"),
    )


def test_the_shadow_schema_always_carries_the_mandatory_prefix():
    assert plan(estate(), scenario()).schema.startswith(SHADOW_PREFIX)


def test_everything_downstream_of_the_fault_must_be_rebuilt():
    layout = plan(estate(), scenario())
    assert layout.to_rebuild == ("intermediate.int_orders_enriched", "marts.mart_revenue_daily")


def test_the_rebuild_set_is_chosen_structurally_not_from_the_prediction():
    """Scope comes from table-grain lineage, which is the widest possible blast radius.

    Scoping the experiment with the column-grain prediction would let it confirm itself.
    """
    layout = plan(estate(), scenario())
    assert set(layout.to_rebuild) == set(estate().reachable_downstream("staging.stg_fx_rates"))


def test_raw_sources_are_never_copied():
    """dbt reads sources where they are, and they are never written to."""
    layout = plan(estate(), scenario())
    assert "raw_pg.fx_rates" not in layout.passthrough
    assert "raw_pg.fx_rates" not in layout.to_rebuild


def test_the_faulted_asset_is_not_a_passthrough():
    layout = plan(estate(), scenario())
    assert layout.faulted == "staging.stg_fx_rates"
    assert "staging.stg_fx_rates" not in layout.passthrough


def test_unrelated_models_are_passthroughs_so_probes_have_healthy_upstreams():
    layout = plan(estate(), scenario())
    assert "staging.stg_orders" in layout.passthrough
    assert "marts.mart_logistics_sla" in layout.passthrough


def test_model_name_drops_the_schema():
    assert model_name("marts.mart_revenue_daily") == "mart_revenue_daily"


# ---------------------------------------------------------------- consumer queries


def test_consumer_queries_are_repointed_at_the_shadow_schema():
    sql = "select a from marts.mart_revenue_daily join ml.feature_txn_velocity using (b)"
    assert repoint(sql, "twin_shadow_x") == (
        "select a from twin_shadow_x.mart_revenue_daily join twin_shadow_x.feature_txn_velocity using (b)"
    )


def test_repointing_leaves_raw_sources_alone():
    """Raw data is never copied, so a query reading it should keep reading the real thing."""
    sql = "select * from raw_pg.orders"
    assert repoint(sql, "twin_shadow_x") == sql
