from pathlib import Path

from twin.context import confidence, rank_candidates
from twin.read.model import Asset, Edge, EstateGraph
from twin.score.usage import Usage
from twin.simulate.scenario import Fault, Scenario


def graph() -> EstateGraph:
    return EstateGraph(
        assets=(
            Asset(
                key="raw.feed", kind="dataset", name="feed", urns=("feed",), layer="raw",
                columns=tuple(), owners=("owner@example.com",), team="platform",
                refresh_cadence="hourly", sla_hours=2, criticality_tier="tier1", replicated=False,
            ),
            Asset(key="marts.output", kind="dataset", name="output", urns=("output",), layer="marts"),
        ),
        edges=(Edge("raw.feed", "marts.output"),), column_edges=(),
        read_at="now", source="test",
    )


def scenario(name: str, asset: str = "raw.feed") -> Scenario:
    return Scenario(
        name=name, title=name, description="", fault=Fault("drop_asset", asset, None, __import__("datetime").time(4)),
        path=Path(name),
    )


def test_context_confidence_exposes_missing_schema_and_usage_evidence():
    result = confidence(graph(), "raw.feed", {})
    assert result.state == "partial"
    assert result.schema == 0.0
    assert result.usage == 0.0
    assert result.ownership == 1.0


def test_campaign_tie_break_is_deterministic_and_novelty_is_measured(tmp_path):
    estate = graph()
    ranked = rank_candidates(
        estate,
        [scenario("b_scenario"), scenario("a_scenario")],
        {"raw.feed": 40.0},
        {"raw.feed": Usage("raw.feed", 10, 1)},
        tmp_path / "evidence.jsonl",
    )
    assert [candidate.scenario.name for candidate in ranked] == ["a_scenario", "b_scenario"]
    assert ranked[0].novelty == 1.0
