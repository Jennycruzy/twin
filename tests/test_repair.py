import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from twin.read.model import Asset, Column, ColumnEdge, Edge, EstateGraph
from twin.repair import RepairError, build_proposal
from twin.simulate.scenario import Fault, Scenario
from twin.target import load_target


def _graph(column_edges=()):
    return EstateGraph(
        assets=(
            Asset(
                key="raw_pg.orders", kind="dataset", name="orders", urns=("orders",), layer="raw",
                columns=(Column("merchant_id", "integer", True), Column("order_id", "integer", False)),
                owners=("platform@example.com",), team="platform", refresh_cadence="daily",
                sla_hours=6, criticality_tier="tier1", replicated=True,
            ),
            Asset(
                key="staging.stg_orders", kind="dataset", name="stg_orders", urns=("stg",), layer="staging",
                columns=(Column("merchant_id", "integer", True),),
            ),
        ),
        edges=(Edge("raw_pg.orders", "staging.stg_orders"),),
        column_edges=column_edges,
        read_at="fixed", source="test",
    )


def _scenario():
    return Scenario(
        name="merchant_id_nulled_at_source",
        title="merchant id disappears",
        description="",
        fault=Fault("null_out_column", "raw_pg.orders", "merchant_id", dt.time(4)),
        path=Path("scenarios/merchant_id_nulled_at_source.yml"),
    )


def _target(tmp_path):
    root = tmp_path / "dbt"
    models = root / "models"
    models.mkdir(parents=True)
    (models / "sources.yml").write_text(
        """version: 2
sources:
  - name: raw_pg
    tables:
      - name: orders
        description: Order headers.
        meta: {owner: platform@example.com}
      - name: customers
        description: Customers.
"""
    )
    return replace(load_target("commerce", Path("targets")), dbt_project=root)


def test_proposal_is_a_specific_reviewable_patch(tmp_path):
    proposal = build_proposal(_graph(), _target(tmp_path), _scenario())

    assert "+        columns:\n+          - name: merchant_id" in proposal.patch
    assert "raw_pg.orders.merchant_id" in proposal.markdown
    assert "staging.stg_orders" in proposal.markdown
    assert proposal.source_file.name == "sources.yml"


def test_proposal_refuses_a_gap_that_is_already_present(tmp_path):
    graph = _graph((ColumnEdge("raw_pg.orders", "merchant_id", "staging.stg_orders", "merchant_id"),))

    with pytest.raises(RepairError, match="already has column lineage"):
        build_proposal(graph, _target(tmp_path), _scenario())


def test_proposal_refuses_a_column_already_declared_in_dbt(tmp_path):
    target = _target(tmp_path)
    source_file = target.dbt_project / "models" / "sources.yml"
    source_file.write_text(source_file.read_text().replace(
        "        meta: {owner: platform@example.com}\n",
        "        meta: {owner: platform@example.com}\n"
        "        columns:\n          - name: merchant_id\n",
    ))

    with pytest.raises(RepairError, match="already declared"):
        build_proposal(_graph(), target, _scenario())
