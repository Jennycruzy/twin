"""Tests for the estate graph and the fold that produces it.

Stage 1's contract with every later stage is the graph, so these tests pin the properties
the rest of the pipeline is entitled to assume: assets are folded from their catalog
entities, ordering never leaks into output, and the fingerprint tracks content and nothing
else. A fingerprint that changed between two identical reads would silently invalidate both
the cache and the nightly trend.

They exercise the pure functions against catalog payloads shaped exactly like the ones
DataHub returns, so they need neither a catalog nor a network. The complementary check that
the real catalog is read correctly is `make read`, which is an integration test by nature.
"""

from __future__ import annotations

import asyncio
import json

from twin.read.materialize import _fold, _key_for, _physical_urn, _table_edges
from twin.read.model import Asset, Column, ColumnEdge, Edge, EstateGraph, layer_of

PG = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.staging.stg_fx_rates,PROD)"
DBT = "urn:li:dataset:(urn:li:dataPlatform:dbt,warehouse.staging.stg_fx_rates,PROD)"
DASH = "urn:li:dashboard:(looker,finance-revenue-review)"


def pg_entity() -> dict:
    """A Postgres dataset as DataHub returns it: real column types, no ownership."""
    return {
        "urn": PG,
        "properties": {"name": "stg_fx_rates", "customProperties": [{"key": "is_view", "value": "True"}]},
        "subTypes": {"typeNames": ["View"]},
        "schemaMetadata": {
            "fields": [
                {"fieldPath": "rate", "nativeDataType": "NUMERIC", "nullable": True},
                {"fieldPath": "rate_date", "nativeDataType": "DATE", "nullable": False},
            ]
        },
    }


def dbt_entity() -> dict:
    """The dbt sibling: ownership, tags and the operational metadata, no column types."""
    return {
        "urn": DBT,
        "properties": {
            "name": "stg_fx_rates",
            "customProperties": [
                {"key": "team", "value": "ml-platform"},
                {"key": "refresh_cadence", "value": "daily_0530"},
                {"key": "sla_hours", "value": "7"},
                {"key": "criticality_tier", "value": "tier2"},
                {"key": "replicated", "value": "false"},
                {"key": "materialization", "value": "view"},
            ],
        },
        "ownership": {"owners": [{"owner": {"urn": "urn:li:corpuser:amara.chen@example.com"}}]},
        "tags": {"tags": [{"tag": {"urn": "urn:li:tag:dbt:tier:tier2"}}]},
    }


# ---------------------------------------------------------------- keys and layers


def test_dataset_key_drops_the_database_and_keeps_the_schema():
    assert _key_for(pg_entity()) == "staging.stg_fx_rates"


def test_siblings_share_one_key():
    assert _key_for(pg_entity()) == _key_for(dbt_entity())


def test_non_dataset_keys_are_namespaced_by_kind():
    entity = {"urn": DASH, "properties": {"name": "finance-revenue-review"}}
    assert _key_for(entity) == "dashboard:finance-revenue-review"


def test_layer_comes_from_the_schema_for_datasets_and_the_kind_for_the_rest():
    assert layer_of("staging.stg_fx_rates", "dataset") == "staging"
    assert layer_of("raw_pg.orders", "dataset") == "raw_pg"
    assert layer_of("dashboard:finance", "dashboard") == "bi"
    assert layer_of("mlModel:fraud_scorer_v3", "mlModel") == "ml"


# ---------------------------------------------------------------- folding


def folded() -> Asset:
    urn_to_key = {PG: "staging.stg_fx_rates", DBT: "staging.stg_fx_rates"}
    assets = _fold([pg_entity(), dbt_entity()], urn_to_key)
    assert len(assets) == 1
    return assets[0]


def test_fold_produces_one_asset_holding_both_urns():
    assert folded().urns == (DBT, PG)


def test_fold_takes_columns_from_postgres_and_metadata_from_dbt():
    asset = folded()
    assert asset.columns == (
        Column("rate", "NUMERIC", True),
        Column("rate_date", "DATE", False),
    )
    assert asset.owners == ("amara.chen@example.com",)
    assert asset.team == "ml-platform"
    assert asset.sla_hours == 7
    assert asset.replicated is False


def test_fold_is_independent_of_the_order_entities_arrive_in():
    urn_to_key = {PG: "staging.stg_fx_rates", DBT: "staging.stg_fx_rates"}
    forwards = _fold([pg_entity(), dbt_entity()], urn_to_key)
    backwards = _fold([dbt_entity(), pg_entity()], urn_to_key)
    assert forwards == backwards


def test_sla_hours_that_is_not_a_number_is_dropped_rather_than_guessed():
    entity = dbt_entity()
    entity["properties"]["customProperties"] = [{"key": "sla_hours", "value": "soon"}]
    asset = _fold([entity], {DBT: "staging.stg_fx_rates"})[0]
    assert asset.sla_hours is None


def test_column_lineage_is_read_from_the_physical_entity():
    asset = folded()
    assert _physical_urn(asset) == PG


# ---------------------------------------------------------------- graph behaviour


def graph() -> EstateGraph:
    def dataset(key: str) -> Asset:
        return Asset(key=key, kind="dataset", name=key, urns=(key,), layer=layer_of(key, "dataset"))

    return EstateGraph(
        assets=(dataset("a"), dataset("b"), dataset("c")),
        edges=(Edge("a", "b"), Edge("b", "c")),
        column_edges=(ColumnEdge("a", "rate", "b"),),
        read_at="2026-08-05T09:00:00+00:00",
        source="http://datahub-gms:8080",
    )


def test_reachability_is_transitive():
    assert graph().reachable_downstream("a") == ("b", "c")


def test_reachability_terminates_on_a_cycle():
    cyclic = EstateGraph(
        assets=graph().assets,
        edges=(Edge("a", "b"), Edge("b", "c"), Edge("c", "a")),
        column_edges=(),
        read_at="2026-08-05T09:00:00+00:00",
        source="test",
    )
    assert cyclic.reachable_downstream("a") == ("a", "b", "c")


def test_column_consumers_are_resolved_per_column():
    assert graph().columns_consuming("a", "rate") == (ColumnEdge("a", "rate", "b"),)
    assert graph().columns_consuming("a", "rate_date") == ()


# ---------------------------------------------------------------- identity


def test_fingerprint_ignores_when_and_where_the_read_happened():
    same_estate_later = EstateGraph(
        assets=graph().assets,
        edges=graph().edges,
        column_edges=graph().column_edges,
        read_at="2027-01-01T00:00:00+00:00",
        source="http://somewhere-else:8080",
    )
    assert same_estate_later.fingerprint == graph().fingerprint


def test_fingerprint_ignores_the_order_assets_and_edges_are_supplied_in():
    reversed_inputs = EstateGraph(
        assets=tuple(reversed(graph().assets)),
        edges=tuple(reversed(graph().edges)),
        column_edges=graph().column_edges,
        read_at=graph().read_at,
        source=graph().source,
    )
    assert reversed_inputs.fingerprint == graph().fingerprint


def test_fingerprint_tracks_a_change_in_operational_metadata():
    base = graph()
    changed = EstateGraph(
        assets=(base.assets[0], base.assets[1], Asset(key="c", kind="dataset", name="c", urns=("c",), layer="other", sla_hours=4)),
        edges=base.edges,
        column_edges=base.column_edges,
        read_at=base.read_at,
        source=base.source,
    )
    assert changed.fingerprint != base.fingerprint


def test_fingerprint_tracks_a_lost_edge():
    base = graph()
    fewer = EstateGraph(
        assets=base.assets,
        edges=(Edge("a", "b"),),
        column_edges=base.column_edges,
        read_at=base.read_at,
        source=base.source,
    )
    assert fewer.fingerprint != base.fingerprint


def test_graph_survives_a_round_trip_through_json():
    restored = EstateGraph.from_dict(json.loads(graph().to_json()))
    assert restored == graph()
    assert restored.fingerprint == graph().fingerprint


def test_serialisation_is_byte_identical_across_runs():
    assert graph().to_json() == graph().to_json()


# ---------------------------------------------------------------- edge assembly


class FakeClient:
    """Stands in for the MCP client, returning fixed upstreams per URN."""

    def __init__(self, upstreams: dict[str, list[str]]) -> None:
        self._upstreams = upstreams

    async def upstreams(self, urn: str, column: str | None = None) -> list[dict]:
        return [{"urn": u} for u in self._upstreams.get(urn, [])]


def test_sibling_lineage_collapses_instead_of_becoming_a_self_edge():
    """Postgres entities report their dbt sibling as an upstream.

    Once folded, both sides are one asset, so that edge is an asset depending on itself.
    Keeping it would give every table in the estate a phantom dependency.
    """
    urn_to_key = {PG: "staging.stg_fx_rates", DBT: "staging.stg_fx_rates"}
    client = FakeClient({PG: [DBT], DBT: []})
    assert asyncio.run(_table_edges(client, [PG, DBT], urn_to_key)) == []


def test_upstreams_pointing_outside_the_modelled_estate_are_dropped():
    urn_to_key = {DBT: "staging.stg_fx_rates"}
    client = FakeClient({DBT: ["urn:li:dataset:(urn:li:dataPlatform:kafka,unmodelled,PROD)"]})
    assert asyncio.run(_table_edges(client, [DBT], urn_to_key)) == []
