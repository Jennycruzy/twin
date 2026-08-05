"""Tests for scenario loading, propagation and grading.

The propagation tests pin the two claims a timeline makes that a plain downstream list does
not: that a dropped column reaches only the assets reading *that* column, and that when each
asset breaks depends on how it is materialised and how often it refreshes.

The grading tests pin the properties that keep the scorecard honest — scope is respected,
misses are counted as misses, and a prediction about something the experiment cannot observe
is excluded rather than quietly scored as correct.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twin.read.model import Asset, ColumnEdge, Edge, EstateGraph
from twin.simulate.propagate import predict
from twin.simulate.scenario import ScenarioError, load_scenario
from twin.verify.grade import grade


def dataset(key: str, materialization: str = "table", cadence: str = "daily_0700") -> Asset:
    return Asset(
        key=key,
        kind="dataset",
        name=key.split(".")[-1],
        urns=(key,),
        layer=key.split(".")[0],
        materialization=materialization,
        refresh_cadence=cadence,
    )


def estate() -> EstateGraph:
    """A small estate with one column that matters and one that does not.

    ``uses_rate`` reads the column that gets dropped; ``ignores_rate`` sits downstream of the
    same table and reads a different column. A table-grain model cannot tell them apart.
    """
    return EstateGraph(
        assets=(
            dataset("staging.fx", materialization="view"),
            dataset("intermediate.uses_rate", cadence="daily_0700"),
            dataset("intermediate.ignores_rate", cadence="daily_0700"),
            dataset("marts.downstream_of_uses", cadence="daily_0800"),
            dataset("marts.view_on_uses", materialization="view"),
            Asset(key="dashboard:finance", kind="dashboard", name="finance", urns=("d",), layer="bi"),
        ),
        edges=(
            Edge("staging.fx", "intermediate.uses_rate"),
            Edge("staging.fx", "intermediate.ignores_rate"),
            Edge("intermediate.uses_rate", "marts.downstream_of_uses"),
            Edge("intermediate.uses_rate", "marts.view_on_uses"),
            Edge("marts.downstream_of_uses", "dashboard:finance"),
        ),
        column_edges=(
            ColumnEdge("staging.fx", "rate", "intermediate.uses_rate", "rate_usd"),
            ColumnEdge("staging.fx", "rate_date", "intermediate.ignores_rate", "as_of"),
        ),
        read_at="2026-08-05T09:00:00+00:00",
        source="test",
    )


def scenario_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "scenario.yml"
    path.write_text(body)
    return path


def drop_rate(tmp_path: Path, at: str = "04:12") -> object:
    return load_scenario(
        scenario_file(
            tmp_path,
            f"name: drop_rate\nfault:\n  kind: drop_column\n  asset: staging.fx\n"
            f"  column: rate\n  at: \"{at}\"\n",
        )
    )


# ---------------------------------------------------------------- scenarios


def test_a_scenario_naming_an_unexecutable_fault_is_rejected(tmp_path):
    body = "name: bad\nfault:\n  kind: set_the_building_on_fire\n  asset: staging.fx\n"
    with pytest.raises(ScenarioError, match="cannot be executed"):
        load_scenario(scenario_file(tmp_path, body))


def test_a_column_drop_must_name_a_column(tmp_path):
    body = "name: bad\nfault:\n  kind: drop_column\n  asset: staging.fx\n"
    with pytest.raises(ScenarioError, match="must name a column"):
        load_scenario(scenario_file(tmp_path, body))


def test_a_scenario_name_that_is_not_a_safe_identifier_is_rejected(tmp_path):
    """The name becomes part of the schema the fault executes in."""
    body = 'name: "drop; DROP SCHEMA marts"\nfault:\n  kind: drop_column\n  asset: a.b\n  column: c\n'
    with pytest.raises(ScenarioError, match="shadow schema"):
        load_scenario(scenario_file(tmp_path, body))


# ---------------------------------------------------------------- propagation


def test_only_the_assets_reading_the_dropped_column_are_the_first_wave(tmp_path):
    timeline = predict(estate(), drop_rate(tmp_path))
    assert timeline.direct == ("intermediate.uses_rate",)


def test_a_sibling_downstream_reading_another_column_is_not_predicted(tmp_path):
    timeline = predict(estate(), drop_rate(tmp_path))
    assert "intermediate.ignores_rate" not in timeline.broken


def test_breakage_follows_lineage_beyond_the_first_wave(tmp_path):
    timeline = predict(estate(), drop_rate(tmp_path))
    assert set(timeline.broken) == {
        "intermediate.uses_rate",
        "marts.downstream_of_uses",
        "marts.view_on_uses",
        "dashboard:finance",
    }


def test_a_table_breaks_at_its_next_refresh_and_a_view_breaks_immediately(tmp_path):
    timeline = predict(estate(), drop_rate(tmp_path))
    uses = timeline.event_for("intermediate.uses_rate")
    view = timeline.event_for("marts.view_on_uses")
    mart = timeline.event_for("marts.downstream_of_uses")

    # Fault at 04:12, so a daily_0700 table breaks 2h48m later.
    assert uses.at == dt.timedelta(hours=2, minutes=48)
    # A view holds nothing of its own, so it goes the moment its upstream does.
    assert view.at == uses.at
    # A daily_0800 table survives until its own build.
    assert mart.at == dt.timedelta(hours=3, minutes=48)


def test_every_predicted_event_carries_a_reason(tmp_path):
    timeline = predict(estate(), drop_rate(tmp_path))
    assert all(event.reason for event in timeline.events)


def test_a_fault_on_an_asset_outside_the_graph_is_an_error(tmp_path):
    body = "name: missing\nfault:\n  kind: drop_column\n  asset: nowhere.at_all\n  column: x\n"
    with pytest.raises(KeyError):
        predict(estate(), load_scenario(scenario_file(tmp_path, body)))


# ---------------------------------------------------------------- grading


def test_grading_counts_hits_misses_and_false_alarms():
    card = grade(
        predicted=["a", "b"],
        observed=["b", "c"],
        scope=["a", "b", "c"],
    )
    assert card.hits == ("b",)
    assert card.false_alarms == ("a",)
    assert card.misses == ("c",)
    assert card.precision == 0.5
    assert card.recall == 0.5


def test_predictions_outside_the_scope_are_not_scored():
    """A dashboard cannot be observed by a dbt build, so it must not count either way."""
    card = grade(predicted=["a", "dashboard:finance"], observed=["a"], scope=["a"])
    assert card.ungraded_predictions == ("dashboard:finance",)
    assert card.precision == 1.0
    assert card.hits == ("a",)


def test_predicting_nothing_gives_no_precision_rather_than_a_perfect_one():
    card = grade(predicted=[], observed=["a"], scope=["a"])
    assert card.precision is None
    assert card.recall == 0.0


def test_a_perfect_score_is_flagged_rather_than_celebrated():
    assert grade(predicted=["a"], observed=["a"], scope=["a"]).is_suspiciously_perfect
    assert not grade(predicted=[], observed=[], scope=["a"]).is_suspiciously_perfect
