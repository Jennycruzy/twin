"""Propagate a fault across the estate and produce an event-ordered timeline.

The model is small enough to state in full, which is the point: every predicted event has a
reason a person can check against the graph.

**What breaks.** A dropped column breaks the assets that read *that column*, which the graph
knows at column grain. From there, breakage follows table-grain lineage: an asset breaks if
anything it reads has broken.

**When it breaks.** Refresh cadence decides. A view has no build of its own, so it breaks the
instant its upstream does — the failure is visible at the next query. A table breaks when it
is next rebuilt, which is its next refresh after its upstream broke, so a nightly mart can
sit healthy for hours after the fault while continuing to serve yesterday's numbers.
Consumers — dashboards, charts, models — read on demand and break with their upstream.

That timing distinction is the reason the estate carries mixed materialisations and mixed
cadences, and it is what makes a timeline more useful than a list.

One honest limitation, repeated in the report Stage 4 prints: the *ordering* here is not
what shadow execution verifies. A dbt build runs the estate at once rather than over the
following day, so verification grades which assets broke, not when. Verifying the clock
would mean holding a warehouse for a simulated day, which Twin does not do.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from twin.faults import UNAVAILABLE, cascade_impact
from twin.read.model import KIND_DATASET, EstateGraph
from twin.simulate.scenario import Scenario

# The clock the timeline is expressed against. Offsets from the fault are what get printed,
# so the anchor date never appears anywhere and cannot be mistaken for a real one.
_EPOCH = dt.datetime(2000, 1, 1)

_CONTINUOUS = "continuous"
_HOURLY = "hourly"
_DAILY_PREFIX = "daily_"

# A continuously-landing pipeline is not instantaneous; it lands in batches minutes apart.
# Five minutes is a declared modelling assumption, not a measurement.
_CONTINUOUS_LAG = dt.timedelta(minutes=5)


@dataclass(frozen=True, order=True)
class Event:
    """One asset failing, at an offset from the fault, in a particular way."""

    at: dt.timedelta
    key: str
    impact: str
    reason: str

    def offset(self) -> str:
        """``+03:18``, or ``+1d 02:40`` once it crosses a day."""
        total = int(self.at.total_seconds())
        days, rest = divmod(total, 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        stamp = f"{hours:02d}:{minutes:02d}"
        return f"+{days}d {stamp}" if days else f"+{stamp}"


@dataclass(frozen=True)
class Timeline:
    """The predicted consequences of one fault."""

    scenario: str
    origin: str
    events: tuple[Event, ...]
    direct: tuple[str, ...]
    """Assets predicted to break because they read the faulted column themselves.

    Kept separate from the rest of the blast radius because it is the claim column-grain
    lineage actually makes, and the only part of the prediction that can be tested without
    the answer being forced. Everything further downstream fails for the trivial reason that
    what it reads is missing — a prediction that agrees with reality there has demonstrated
    very little.
    """

    @property
    def broken(self) -> tuple[str, ...]:
        """Every asset predicted to fail, excluding the asset the fault was applied to."""
        return tuple(sorted({e.key for e in self.events}))

    def with_impact(self, impact: str) -> tuple[str, ...]:
        """Assets predicted to fail in one particular way.

        Graded separately from the total, because predicting that something breaks when it
        actually goes quietly wrong is a real error that a combined count would hide.
        """
        return tuple(sorted({e.key for e in self.events if e.impact == impact}))

    def event_for(self, key: str) -> Event | None:
        return next((e for e in self.events if e.key == key), None)


def _next_refresh(cadence: str | None, after: dt.datetime) -> dt.datetime:
    """When an asset with this cadence next rebuilds, at or after ``after``.

    An unknown or absent cadence is treated as continuous rather than as never refreshing.
    Assuming an asset never rebuilds would silently remove it and everything beneath it from
    the blast radius, which is the direction of error that makes a prediction look good.
    """
    if not cadence or cadence == _CONTINUOUS:
        return after + _CONTINUOUS_LAG
    if cadence == _HOURLY:
        candidate = after.replace(minute=0, second=0, microsecond=0)
        return candidate if candidate >= after else candidate + dt.timedelta(hours=1)
    if cadence.startswith(_DAILY_PREFIX):
        clock = cadence.removeprefix(_DAILY_PREFIX)
        try:
            hour, minute = int(clock[:2]), int(clock[2:])
        except ValueError:
            return after + _CONTINUOUS_LAG
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate >= after else candidate + dt.timedelta(days=1)
    return after + _CONTINUOUS_LAG


def _breaks_on_read(graph: EstateGraph, key: str) -> bool:
    """Whether this asset fails the moment its upstream does, rather than at a rebuild.

    Views hold no data of their own, and consumers read through to whatever is underneath.
    Tables keep serving what they last built.
    """
    asset = graph.asset(key)
    if asset.kind != KIND_DATASET:
        return True
    return (asset.materialization or "").lower() == "view"


def _all_columns(graph: EstateGraph, key: str) -> frozenset[str]:
    return frozenset(c.name for c in graph.asset(key).columns)


def _first_wave(
    graph: EstateGraph, scenario: Scenario
) -> tuple[dict[str, tuple[str, frozenset[str]]], str]:
    """The assets the fault reaches directly, why, and which of their columns it corrupts.

    A column-grained fault reaches only the readers of that column, which is the claim
    column-level lineage exists to make, and corrupts only the columns those readers derived
    from it. A table-grained fault — the asset is deleted, the asset stops updating — reaches
    every consumer and affects all of their columns, because there is no column to
    discriminate on.
    """
    origin = scenario.fault.asset
    definition = scenario.fault.definition

    if definition.is_column_grained:
        column = scenario.fault.column or ""
        landings: dict[str, set[str]] = {}
        for edge in graph.columns_consuming(origin, column):
            landings.setdefault(edge.target, set()).add(edge.target_column)
        return (
            {
                key: (f"reads {origin}.{column}", frozenset(columns))
                for key, columns in landings.items()
            },
            definition.impact,
        )

    return (
        {
            key: (f"reads {origin}", _all_columns(graph, key))
            for key in graph.downstream(origin)
        },
        definition.impact,
    )


def _spread(
    graph: EstateGraph, key: str, corrupted: frozenset[str], impact: str
) -> list[tuple[str, frozenset[str], str]]:
    """Where damage in ``key`` goes next, and what it corrupts when it gets there.

    The two impacts spread differently, and conflating them is what makes a propagation model
    over-predict.

    An asset that is **unavailable** takes every consumer with it whatever columns they read:
    a relation that is not there cannot be read at all. That is table grain, and it is right.

    An asset that is **degraded** carries wrong values in particular columns, and corrupts
    only the downstream columns derived from those. A mart that never touches the affected
    column is genuinely fine, and predicting otherwise is a false alarm that costs the report
    its credibility.

    Where an asset has no column lineage at all, damage falls back to table grain rather than
    stopping. Missing a real failure is the more expensive error, so the absence of
    information produces caution rather than silence.
    """
    if impact == UNAVAILABLE:
        return [(t, _all_columns(graph, t), f"reads {key}") for t in graph.downstream(key)]

    outgoing = [e for e in graph.column_edges if e.source == key]
    if not outgoing:
        return [(t, _all_columns(graph, t), f"reads {key}") for t in graph.downstream(key)]

    landings: dict[str, set[str]] = {}
    for edge in outgoing:
        if edge.source_column in corrupted:
            landings.setdefault(edge.target, set()).add(edge.target_column)
    return [
        (target, frozenset(columns), f"reads {key}.{sorted(corrupted & _sources_into(outgoing, target))[0]}")
        for target, columns in landings.items()
    ]


def _sources_into(edges: list, target: str) -> set[str]:
    return {e.source_column for e in edges if e.target == target}


def predict(graph: EstateGraph, scenario: Scenario) -> Timeline:
    """Propagate the scenario's fault across the graph."""
    origin = scenario.fault.asset
    if not graph.has(origin):
        raise KeyError(f"{origin} is not in the estate graph")

    fault_at = _EPOCH.replace(hour=scenario.fault.at.hour, minute=scenario.fault.at.minute)
    wave, impact = _first_wave(graph, scenario)

    failed_at: dict[str, dt.datetime] = {}
    reasons: dict[str, str] = {}
    impacts: dict[str, str] = {}
    corrupted: dict[str, frozenset[str]] = {}
    frontier: list[tuple[str, dt.datetime, str, str, frozenset[str]]] = [
        (key, fault_at, reason, impact, columns) for key, (reason, columns) in sorted(wave.items())
    ]

    while frontier:
        frontier.sort(key=lambda item: (item[1], item[0]))
        key, upstream_failed_at, reason, upstream_impact, columns = frontier.pop(0)
        if key == origin or not graph.has(key):
            continue

        when = (
            upstream_failed_at
            if _breaks_on_read(graph, key)
            else _next_refresh(graph.asset(key).refresh_cadence, upstream_failed_at)
        )
        # Revisited only when this path is earlier, or when it corrupts something the
        # earlier path did not — damage arriving by two routes is the union of both.
        already = corrupted.get(key, frozenset())
        if key in failed_at and failed_at[key] <= when and columns <= already:
            continue

        failed_at[key] = min(when, failed_at.get(key, when))
        reasons[key] = reason
        impacts[key] = upstream_impact
        corrupted[key] = already | columns
        for target, landing, why in _spread(graph, key, corrupted[key], upstream_impact):
            frontier.append((target, when, why, cascade_impact(upstream_impact), landing))

    events = tuple(
        sorted(
            Event(at=when - fault_at, key=key, impact=impacts[key], reason=reasons[key])
            for key, when in failed_at.items()
        )
    )
    return Timeline(
        scenario=scenario.name,
        origin=origin,
        events=events,
        direct=tuple(sorted(wave)),
    )
