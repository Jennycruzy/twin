"""Tests for the incidents Twin raises against proven failures.

The rule under test is the one that separates an incident from a prediction: Twin may only
raise an incident for something it *observed*. Stage 2 predicts, Stage 4 executes, and only
Stage 4's observations are allowed to reach the catalog. A tool that filed incidents for
predictions would be filling somebody's estate with alerts for things that did not happen,
and no amount of accuracy elsewhere would make that acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from twin.faults import DEGRADED, UNAVAILABLE
from twin.verify.observe import AssetObservation
from twin.write.incidents import URN_PREFIX, incident_urn, raise_for, raised_on


@dataclass
class FakeCatalog:
    """Records what would have been written, so the rules can be tested without DataHub."""

    emitted: list = None
    graph: object = None

    def __post_init__(self):
        self.emitted = []

    def _emit(self, urn, aspect):
        self.emitted.append((urn, aspect))


def urns_for(key: str) -> tuple[str, ...]:
    if key == "orphan":
        return ()
    return (f"urn:li:dataset:(urn:li:dataPlatform:postgres,{key},PROD)",
            f"urn:li:dataset:(urn:li:dataPlatform:dbt,{key},PROD)")


def observation(key: str, impact: str | None, detail: str = "boom") -> AssetObservation:
    return AssetObservation(key=key, impact=impact, detail=detail)


def test_only_observed_failures_are_raised():
    """An asset that came through the fault healthy gets no incident."""
    catalog = FakeCatalog()
    raised = raise_for(
        catalog,
        "fx_rate_column_drop",
        {
            "marts.broke": observation("marts.broke", UNAVAILABLE),
            "marts.fine": observation("marts.fine", None),
        },
        urns_for,
        "provenance",
    )
    assert [r.key for r in raised] == ["marts.broke"]


def test_an_asset_with_no_urn_is_skipped_rather_than_invented():
    catalog = FakeCatalog()
    raised = raise_for(
        catalog, "s", {"orphan": observation("orphan", UNAVAILABLE)}, urns_for, "p"
    )
    assert raised == ()
    assert catalog.emitted == []


def test_the_warehouse_error_is_carried_verbatim():
    """The evidence is the point. A summarised error is a claim; the raw one is proof."""
    catalog = FakeCatalog()
    error = 'relation "twin_shadow_x.mart_revenue_daily" does not exist'
    raise_for(
        catalog, "s", {"m": observation("m", UNAVAILABLE, error)}, urns_for, "p"
    )
    _, info = catalog.emitted[0]
    assert error in info.description


def test_the_description_says_this_was_observed_not_predicted():
    catalog = FakeCatalog()
    raise_for(catalog, "s", {"m": observation("m", UNAVAILABLE)}, urns_for, "p")
    _, info = catalog.emitted[0]
    assert "observed failure, not a prediction" in info.description


def test_the_incident_is_attached_to_every_sibling_of_the_asset():
    """A person may open the warehouse table or its dbt sibling; the incident is on both."""
    catalog = FakeCatalog()
    raise_for(catalog, "s", {"m": observation("m", UNAVAILABLE)}, urns_for, "p")
    _, info = catalog.emitted[0]
    assert len(info.entities) == 2


def test_impact_selects_the_incident_type():
    catalog = FakeCatalog()
    raise_for(
        catalog,
        "s",
        {"a": observation("a", UNAVAILABLE), "b": observation("b", DEGRADED)},
        urns_for,
        "p",
    )
    types = {urn: info.type for urn, info in catalog.emitted}
    assert types[incident_urn("s", "a")] == "OPERATIONAL"
    assert types[incident_urn("s", "b")] == "FIELD"


def test_the_urn_is_deterministic_so_a_rerun_updates_instead_of_duplicating():
    """Two runs of one scenario must not leave a reader looking at two of each incident."""
    assert incident_urn("fx", "marts.x") == incident_urn("fx", "marts.x")
    assert incident_urn("fx", "marts.x") != incident_urn("fx", "marts.y")
    assert incident_urn("fx", "marts.x").startswith(URN_PREFIX)


def test_urns_carry_no_dots_because_datahub_normalises_them():
    assert "." not in incident_urn("fx", "marts.mart_revenue_daily").removeprefix(URN_PREFIX)


class FakeGraph:
    """Answers the incidents-on-asset query the way GMS does."""

    def __init__(self, payload):
        self.payload = payload

    def execute_graphql(self, query, variables):
        return self.payload


def test_resolution_finds_twins_incidents_on_the_asset():
    """Read back from the asset, because incidents are not top-level entities in GraphQL."""
    catalog = FakeCatalog()
    catalog.graph = FakeGraph(
        {
            "dataset": {
                "incidents": {
                    "incidents": [
                        {"urn": incident_urn("fx", "marts.a"), "status": {"state": "ACTIVE"}},
                        {"urn": "urn:li:incident:someone-elses", "status": {"state": "ACTIVE"}},
                    ]
                }
            }
        }
    )
    sweep = raised_on(catalog, ("urn:li:dataset:(x,y,PROD)",))
    assert sweep.found == (incident_urn("fx", "marts.a"),)
    assert sweep.is_complete


def test_resolution_never_touches_an_incident_twin_did_not_raise():
    catalog = FakeCatalog()
    catalog.graph = FakeGraph(
        {"dataset": {"incidents": {"incidents": [{"urn": "urn:li:incident:abc"}]}}}
    )
    assert raised_on(catalog, ("urn:li:dataset:(x,y,PROD)",)).found == ()


def test_an_asset_with_no_incidents_does_not_break_the_sweep():
    catalog = FakeCatalog()
    catalog.graph = FakeGraph({"dataset": None})
    sweep = raised_on(catalog, ("urn:li:dataset:(x,y,PROD)",))
    assert sweep.found == ()
    assert sweep.is_complete


class FailingGraph:
    def execute_graphql(self, query, variables):
        raise RuntimeError("GMS is down")


def test_an_unreadable_asset_is_reported_not_treated_as_clean():
    """The distinction that matters: nothing found, versus could not look."""
    catalog = FakeCatalog()
    catalog.graph = FailingGraph()
    sweep = raised_on(catalog, ("urn:li:dataset:(x,y,PROD)",))
    assert sweep.found == ()
    assert [urn for urn, _ in sweep.unreachable] == ["urn:li:dataset:(x,y,PROD)"]
    assert not sweep.is_complete


def test_an_unreadable_asset_keeps_the_reason_it_could_not_be_read():
    """The error is the evidence, the same way PostgreSQL's is in Stage 4."""
    catalog = FakeCatalog()
    catalog.graph = FailingGraph()
    (_, error), = raised_on(catalog, ("urn:li:dataset:(x,y,PROD)",)).unreachable
    assert "GMS is down" in error


def test_a_truncated_page_is_reported_rather_than_silently_short():
    catalog = FakeCatalog()
    catalog.graph = FakeGraph(
        {"dataset": {"incidents": {"total": 900, "incidents": [{"urn": "urn:li:incident:a"}]}}}
    )
    sweep = raised_on(catalog, ("urn:li:dataset:(x,y,PROD)",))
    assert sweep.truncated == ("urn:li:dataset:(x,y,PROD)",)
    assert not sweep.is_complete
