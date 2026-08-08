"""Tests for the fault vocabulary, its execution, and the paging list.

The distinction these pin down is the one the verifier relies on: a fault that makes an asset
*unavailable* is not the same as one that leaves it present and *degraded*, and a model that
collapses the two would be right about the loud failures and wrong about the expensive ones.

The SQL tests assert what the execution layer sends rather than mocking a warehouse. Whether
the statements do what they claim is settled by `make run`, which executes them for real.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twin.faults import DEGRADED, KINDS, UNAVAILABLE, cascade_impact, kind
from twin.read.model import Asset, Column, ColumnEdge, Edge, EstateGraph
from twin.simulate.paging import build as build_paging
from twin.simulate.propagate import predict
from twin.simulate.scenario import Fault, Scenario
from twin.verify.faults import FaultNotExecutable, faulted_relation_sql

ORIGIN = "staging.stg_orders"


def asset(key: str, **kwargs) -> Asset:
    return Asset(
        key=key,
        kind=kwargs.pop("kind", "dataset"),
        name=key.split(".")[-1],
        urns=(key,),
        layer=key.split(".")[0],
        **kwargs,
    )


def orders() -> Asset:
    return asset(
        ORIGIN,
        columns=(
            Column("order_id", "integer", False),
            Column("order_date", "date", True),
            Column("merchant_id", "integer", True),
            Column("channel", "text", True),
        ),
    )


def fault(kind_name: str, column: str | None = None, **kwargs) -> Fault:
    return Fault(kind=kind_name, asset=ORIGIN, column=column, at=dt.time(5, 30), **kwargs)


def sql_for(kind_name: str, column: str | None = None, **kwargs) -> str | None:
    return faulted_relation_sql(
        fault(kind_name, column, **kwargs), orders(), "twin_shadow_s", "staging", "stg_orders"
    )


# ---------------------------------------------------------------- the vocabulary


def test_every_declared_fault_kind_has_an_execution():
    """A fault the simulator understands and the warehouse cannot run is unprovable."""
    for name, definition in KINDS.items():
        column = "order_date" if definition.needs_column else None
        # drop_asset is executed by absence, so None is the correct statement for it.
        assert sql_for(name, column) is not None or name == "drop_asset"


def test_loud_and_quiet_failures_are_distinguished():
    assert kind("drop_asset").impact == UNAVAILABLE
    assert kind("stop_new_rows").impact == DEGRADED
    assert kind("null_out_column").impact == DEGRADED


def test_degradation_stays_degradation_downstream():
    """A wrong number is passed on as a wrong number, never as an outage."""
    assert cascade_impact(DEGRADED) == DEGRADED
    assert cascade_impact(UNAVAILABLE) == UNAVAILABLE


# ---------------------------------------------------------------- execution


def test_dropping_a_column_selects_the_survivors():
    statement = sql_for("drop_column", "merchant_id")
    assert '"order_id", "order_date", "channel"' in statement
    assert "merchant_id" not in statement.split("FROM")[0]


def test_deleting_an_asset_creates_nothing():
    assert sql_for("drop_asset") is None


def test_a_type_regression_casts_only_the_named_column():
    statement = sql_for("change_column_type", "order_id")
    assert '"order_id"::text AS "order_id"' in statement
    assert '"merchant_id"::text' not in statement


def test_casting_a_text_column_to_text_is_refused_as_not_a_fault():
    with pytest.raises(FaultNotExecutable, match="already text"):
        sql_for("change_column_type", "channel")


def test_nulling_a_column_keeps_its_type():
    statement = sql_for("null_out_column", "merchant_id")
    assert 'NULL::integer AS "merchant_id"' in statement


def test_staleness_withholds_recent_rows_relative_to_the_data():
    """Cut relative to the data's own maximum, so the scenario does not expire."""
    statement = sql_for("stop_new_rows", "order_date", withhold_days=3)
    assert "MAX(\"order_date\") - INTERVAL '3 days'" in statement
    assert "WHERE" in statement


def test_a_fault_naming_an_absent_column_is_refused():
    with pytest.raises(FaultNotExecutable, match="has no column"):
        sql_for("drop_column", "not_a_column")


def test_every_generated_statement_targets_the_shadow_schema():
    for name, definition in KINDS.items():
        statement = sql_for(name, "order_date" if definition.needs_column else None)
        if statement is not None:
            assert statement.startswith('CREATE VIEW "twin_shadow_s"."stg_orders"')


# ---------------------------------------------------------------- typed propagation


def estate() -> EstateGraph:
    return EstateGraph(
        assets=(
            orders(),
            asset("intermediate.reads_merchant", criticality_tier="tier1", owners=("ana@example.com",)),
            asset("intermediate.reads_date", criticality_tier="tier3", owners=("bo@example.com",)),
            asset("marts.downstream", criticality_tier="tier1", owners=("ana@example.com",)),
            asset("marts.unowned_mart", criticality_tier="tier2"),
            asset("dashboard:finance", kind="dashboard", owners=("cy@example.com",)),
        ),
        edges=(
            Edge(ORIGIN, "intermediate.reads_merchant"),
            Edge(ORIGIN, "intermediate.reads_date"),
            Edge("intermediate.reads_merchant", "marts.downstream"),
            Edge("intermediate.reads_date", "marts.unowned_mart"),
            Edge("marts.downstream", "dashboard:finance"),
        ),
        column_edges=(
            ColumnEdge(ORIGIN, "merchant_id", "intermediate.reads_merchant", "merchant_id"),
            ColumnEdge(ORIGIN, "order_date", "intermediate.reads_date", "order_date"),
        ),
        read_at="2026-08-05T09:00:00+00:00",
        source="test",
    )


def scenario(kind_name: str, column: str | None = None) -> Scenario:
    return Scenario(
        name="s",
        title="t",
        description="",
        fault=fault(kind_name, column),
        path=Path("scenarios/s.yml"),
    )


def test_a_column_fault_reaches_only_that_columns_readers():
    timeline = predict(estate(), scenario("drop_column", "merchant_id"))
    assert timeline.direct == ("intermediate.reads_merchant",)
    assert "intermediate.reads_date" not in timeline.broken


def test_a_table_fault_reaches_every_consumer():
    """Deleting an asset leaves no column to discriminate on."""
    timeline = predict(estate(), scenario("drop_asset"))
    assert set(timeline.direct) == {"intermediate.reads_merchant", "intermediate.reads_date"}


def test_a_staleness_fault_predicts_degradation_and_never_an_outage():
    timeline = predict(estate(), scenario("stop_new_rows", "order_date"))
    assert timeline.with_impact(UNAVAILABLE) == ()
    assert set(timeline.with_impact(DEGRADED)) == set(timeline.broken)


def test_a_deletion_predicts_outage_and_never_mere_degradation():
    timeline = predict(estate(), scenario("drop_asset"))
    assert timeline.with_impact(DEGRADED) == ()


def carrying_estate() -> EstateGraph:
    """An estate where the mart reads a column that has nothing to do with the fault.

    ``marts.downstream`` derives its only column from ``reads_merchant.amount``. A null in
    ``stg_orders.merchant_id`` corrupts ``reads_merchant.merchant_id`` and stops there — the
    mart's numbers are untouched, and a model that follows table lineage cannot tell.
    """
    base = estate()
    return EstateGraph(
        assets=base.assets,
        edges=base.edges,
        column_edges=base.column_edges
        + (
            ColumnEdge("intermediate.reads_merchant", "amount", "marts.downstream", "total"),
            ColumnEdge("intermediate.reads_date", "order_date", "marts.unowned_mart", "day"),
        ),
        read_at=base.read_at,
        source=base.source,
    )


def test_degradation_stops_where_the_corrupted_column_does_not_flow():
    """The fix for the false alarms on merchant_id_nulled.

    Damage that is merely wrong values travels only along the columns derived from those
    values. The mart downstream reads a different column, so it is genuinely fine.
    """
    timeline = predict(carrying_estate(), scenario("null_out_column", "merchant_id"))
    assert "intermediate.reads_merchant" in timeline.broken
    assert "marts.downstream" not in timeline.broken


def test_an_outage_still_reaches_everything_downstream():
    """A missing relation cannot be read at all, whichever column you wanted from it."""
    timeline = predict(carrying_estate(), scenario("drop_column", "merchant_id"))
    assert "marts.downstream" in timeline.broken


def test_damage_falls_back_to_table_grain_where_column_lineage_is_absent():
    """No information is a reason for caution, not for silence.

    ``estate()`` has no column edges leaving the intermediate models, so a degrading fault
    must still be assumed to reach what is downstream of them. Under-predicting a quiet
    failure is the more expensive error.
    """
    timeline = predict(estate(), scenario("null_out_column", "merchant_id"))
    assert "marts.downstream" in timeline.broken


# ---------------------------------------------------------------- paging


def test_paging_orders_by_when_the_phone_rings():
    paging = build_paging(estate(), predict(estate(), scenario("drop_asset")))
    assert [p.at for p in paging.pages] == sorted(p.at for p in paging.pages)


def test_an_owner_is_paged_once_for_everything_they_own():
    paging = build_paging(estate(), predict(estate(), scenario("drop_asset")))
    ana = next(p for p in paging.pages if p.owner == "ana@example.com")
    assert set(ana.assets) == {"intermediate.reads_merchant", "marts.downstream"}


def test_assets_with_no_owner_are_listed_rather_than_dropped():
    """An asset that pages nobody is more dangerous than one that does, not less."""
    paging = build_paging(estate(), predict(estate(), scenario("drop_asset")))
    assert paging.unowned == ("marts.unowned_mart",)


def test_departed_owner_becomes_unowned_in_the_paging_result():
    timeline = predict(estate(), scenario("drop_asset"))
    paging = build_paging(estate(), timeline, departed_owner="ana@example.com")
    assert all(page.owner != "ana@example.com" for page in paging.pages)
    assert "intermediate.reads_merchant" in paging.unowned
    assert "marts.downstream" in paging.unowned


def test_paging_is_stable_across_runs():
    timeline = predict(estate(), scenario("drop_asset"))
    assert build_paging(estate(), timeline) == build_paging(estate(), timeline)
