"""The shadow estate: a disposable copy of the platform with one thing genuinely broken.

Stage 4's claim is that Twin does not ask you to trust a simulation. Making that true means
the fault has to be executed against a real warehouse and the real downstream models have to
be rebuilt against it — which means somewhere there must be a copy of the estate it is safe
to break.

The copy is built out of views, not data. Every model outside the fault's reach becomes a
view onto the real relation, so cloning 1.9M rows is never necessary and the shadow estate
stands up in seconds. Only two things differ from production:

* the faulted asset, which is recreated without the dropped column, and
* everything downstream of it, which is rebuilt for real so that its failures are real
  failures rather than stale copies.

The set that must be rebuilt is chosen structurally — everything downstream of the fault in
table-grain lineage, which is the widest possible blast radius and the same criterion for
every fault. The prediction being graded is a subset of that, chosen at column grain. If the
experiment were scoped using the prediction, it could only ever confirm itself.

The shadow estate serves two experiments in sequence, which is why the passthrough views
cover every model rather than only the untouched ones:

**Probes**, run first. Each downstream model is built on its own while every other model is
still a healthy passthrough onto production. A model that fails here fails because it reads
the dropped column, not because something upstream of it is missing. This is the experiment
that can prove the prediction wrong.

**The cascade**, run second, after the downstream passthroughs are dropped. dbt rebuilds the
whole downstream estate at once, so failures propagate the way they would in a real refresh
and the consumer queries meet the same missing relations a person would.

Teardown runs in a ``finally``. Two consecutive runs leave zero residue.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Iterator

from twin.read.model import KIND_DATASET, EstateGraph
from twin.simulate.scenario import Scenario
from twin.verify.guard import SHADOW_PREFIX
from twin.verify.warehouse import ShadowConnection, columns_clause, literal, qualified

# Layers that are landed by ingestion rather than built by dbt. They are sources, not
# models: dbt reads them where they are, so the shadow estate never needs a copy.
_SOURCE_LAYERS = ("raw_pg", "raw_events")


@dataclass(frozen=True)
class ShadowEstate:
    """What was created, and what dbt is expected to rebuild into it."""

    schema: str
    faulted: str
    dropped_column: str | None
    passthrough: tuple[str, ...]
    to_rebuild: tuple[str, ...]


def schema_name(scenario: Scenario) -> str:
    """The shadow schema for a scenario.

    Deterministic rather than random: a run that crashes leaves a schema whose name is
    predictable, the next run of the same scenario clears it, and nothing accumulates. The
    prefix is not interpolated from anything — it comes from the guard, which is the module
    that refuses to touch anything without it.
    """
    return f"{SHADOW_PREFIX}{scenario.name}"


def _is_model(graph: EstateGraph, key: str) -> bool:
    """Whether dbt builds this asset, as opposed to ingestion landing it."""
    asset = graph.asset(key)
    return asset.kind == KIND_DATASET and asset.layer not in _SOURCE_LAYERS


def model_name(key: str) -> str:
    """``staging.stg_fx_rates`` -> ``stg_fx_rates``, which is what dbt calls it."""
    return key.split(".")[-1]


def _source_schema(key: str) -> str:
    return key.split(".")[0]


def plan(graph: EstateGraph, scenario: Scenario) -> ShadowEstate:
    """Decide what the shadow estate contains, without touching the warehouse."""
    origin = scenario.fault.asset
    downstream = {k for k in graph.reachable_downstream(origin) if _is_model(graph, k)}
    models = {a.key for a in graph.of_kind(KIND_DATASET) if _is_model(graph, a.key)}

    return ShadowEstate(
        schema=schema_name(scenario),
        faulted=origin,
        dropped_column=scenario.fault.column,
        passthrough=tuple(sorted(models - {origin})),
        to_rebuild=tuple(sorted(downstream)),
    )


@contextlib.contextmanager
def shadow_estate(
    graph: EstateGraph, scenario: Scenario, connection: ShadowConnection
) -> Iterator[ShadowEstate]:
    """Stand the shadow estate up, hand it over, and tear it down whatever happens."""
    layout = plan(graph, scenario)

    _drop_schema(connection, layout.schema)
    connection.execute(f"CREATE SCHEMA {_quote_schema(layout.schema)}")
    try:
        for key in layout.passthrough:
            connection.execute(
                f"CREATE VIEW {qualified(layout.schema, model_name(key))} AS "
                f"SELECT * FROM {qualified(_source_schema(key), model_name(key))}"
            )
        _apply_fault(graph, scenario, connection, layout)
        yield layout
    finally:
        _drop_schema(connection, layout.schema)


def _apply_fault(
    graph: EstateGraph,
    scenario: Scenario,
    connection: ShadowConnection,
    layout: ShadowEstate,
) -> None:
    """Execute the declared fault against the shadow copy.

    The surviving column list comes from the graph Stage 1 read, so a scenario naming a
    column that does not exist fails here rather than silently producing a copy identical to
    production and a verification that grades nothing.
    """
    origin = graph.asset(layout.faulted)
    names = [c.name for c in origin.columns]
    if layout.dropped_column not in names:
        raise ValueError(
            f"{layout.faulted} has no column {layout.dropped_column!r} "
            f"(columns: {', '.join(names)})"
        )

    surviving = [name for name in names if name != layout.dropped_column]
    connection.execute(
        f"CREATE VIEW {qualified(layout.schema, model_name(layout.faulted))} AS "
        f"SELECT {columns_clause(surviving)} "
        f"FROM {qualified(_source_schema(layout.faulted), model_name(layout.faulted))}"
    )


def create_passthrough(connection: ShadowConnection, layout: ShadowEstate, key: str) -> None:
    """Point a shadow relation back at the real one.

    Used to restore a model after a probe, so that one probe's failure cannot leave a hole
    that makes the next probe fail for a reason that has nothing to do with the fault.
    """
    name = model_name(key)
    drop_relation(connection, layout, name)
    connection.execute(
        f"CREATE VIEW {qualified(layout.schema, name)} AS "
        f"SELECT * FROM {qualified(_source_schema(key), name)}"
    )


def drop_relation(connection: ShadowConnection, layout: ShadowEstate, name: str) -> None:
    """Drop a shadow relation whatever kind it currently is.

    A probe turns a passthrough view into a table when it succeeds, so the kind cannot be
    assumed. PostgreSQL refuses ``DROP TABLE`` on a view and vice versa even with
    ``IF EXISTS``, so the catalog is asked rather than guessed at.
    """
    rows = connection.fetch(
        "SELECT table_type FROM information_schema.tables "
        f"WHERE table_schema = {literal(layout.schema)} AND table_name = {literal(name)}"
    )
    if not rows:
        return
    kind = "VIEW" if rows[0][0] == "VIEW" else "TABLE"
    connection.execute(f"DROP {kind} IF EXISTS {qualified(layout.schema, name)} CASCADE")


def _quote_schema(schema: str) -> str:
    return '"' + schema.replace('"', '""') + '"'


def _drop_schema(connection: ShadowConnection, schema: str) -> None:
    connection.execute(f"DROP SCHEMA IF EXISTS {_quote_schema(schema)} CASCADE")
