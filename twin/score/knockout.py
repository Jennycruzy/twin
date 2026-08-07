"""The knockout sweep: delete every asset in turn and see what it takes with it.

Fragility could be estimated from graph shape alone — count the downstream nodes, rank by
the count. That measure is cheap, and on this estate it is wrong: `raw_pg.orders` reaches
more assets than `raw_pg.fx_rates` and is the less fragile of the two, because it has a
standby and fx_rates does not.

So the sweep does not count edges. It asks the propagation model the same question the verifier
executes for real — *what happens when this asset is gone* — once per asset, and reads the
answer off the timeline. The consequence is that scoring and verification share one model:
a knockout that claims eleven assets fall over is a claim shadow execution can be pointed at
and made to prove or disprove, which is not true of a number derived from adjacency.

Every knockout is a `drop_asset` fault, because deletion is the fault with no ambiguity —
nothing survives it, so the result is the asset's maximum blast radius rather than its
behaviour under one particular kind of damage.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from twin.read.model import KIND_DATASET, EstateGraph
from twin.simulate.paging import build as build_paging
from twin.simulate.propagate import Timeline, predict
from twin.simulate.scenario import Fault, Scenario

# Knockouts are evaluated at a fixed hour so that two sweeps of the same estate produce the
# same timings. The hour itself is arbitrary and never displayed as a real clock time.
_SWEEP_HOUR = dt.time(4, 0)


@dataclass(frozen=True)
class Knockout:
    """What the estate loses when one asset is deleted."""

    key: str
    timeline: Timeline
    datasets_lost: tuple[str, ...]
    consumers_lost: tuple[str, ...]
    owners_paged: tuple[str, ...]
    unowned_in_radius: tuple[str, ...]
    first_consumer_at: dt.timedelta | None

    @property
    def blast(self) -> int:
        return len(self.datasets_lost) + len(self.consumers_lost)


def _scenario_for(key: str) -> Scenario:
    return Scenario(
        name="knockout",
        title=f"{key} is deleted",
        description="",
        fault=Fault(kind="drop_asset", asset=key, column=None, at=_SWEEP_HOUR),
        path=Path("(knockout sweep)"),
    )


def knockout(graph: EstateGraph, key: str) -> Knockout:
    """Delete one asset, in simulation, and describe the damage."""
    timeline = predict(graph, _scenario_for(key))
    paging = build_paging(graph, timeline)

    datasets = tuple(k for k in timeline.broken if graph.asset(k).kind == KIND_DATASET)
    consumers = tuple(k for k in timeline.broken if graph.asset(k).kind != KIND_DATASET)

    # When the first thing a person looks at goes dark. An asset whose damage surfaces on a
    # dashboard within minutes is a different risk from one that surfaces tomorrow morning,
    # even when the blast radius is identical.
    consumer_events = [e for e in timeline.events if graph.asset(e.key).kind != KIND_DATASET]
    first_consumer = min((e.at for e in consumer_events), default=None)

    return Knockout(
        key=key,
        timeline=timeline,
        datasets_lost=datasets,
        consumers_lost=consumers,
        owners_paged=tuple(p.owner for p in paging.pages),
        unowned_in_radius=paging.unowned,
        first_consumer_at=first_consumer,
    )


def sweep(graph: EstateGraph) -> tuple[Knockout, ...]:
    """Knock out every dataset in the estate, in a fixed order."""
    return tuple(knockout(graph, asset.key) for asset in graph.of_kind(KIND_DATASET))
