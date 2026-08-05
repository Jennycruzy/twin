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

from twin.read.model import KIND_DATASET, EstateGraph
from twin.simulate.scenario import DROP_COLUMN, Scenario

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
    """One asset breaking, at an offset from the fault."""

    at: dt.timedelta
    key: str
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
        """Every asset predicted to break, excluding the asset the fault was applied to."""
        return tuple(sorted({e.key for e in self.events}))

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


def predict(graph: EstateGraph, scenario: Scenario) -> Timeline:
    """Propagate the scenario's fault across the graph."""
    origin = scenario.fault.asset
    if not graph.has(origin):
        raise KeyError(f"{origin} is not in the estate graph")
    if scenario.fault.kind != DROP_COLUMN:
        raise NotImplementedError(f"no propagation model for {scenario.fault.kind}")

    fault_at = _EPOCH.replace(hour=scenario.fault.at.hour, minute=scenario.fault.at.minute)

    # The fault reaches only the assets that read the dropped column. Everything else
    # downstream of the origin is untouched, which is the whole reason to read column
    # lineage — and the reason a wrong column here produces a visibly wrong prediction.
    column = scenario.fault.column or ""
    first_wave = {edge.target for edge in graph.columns_consuming(origin, column)}

    broken_at: dict[str, dt.datetime] = {}
    reasons: dict[str, str] = {}
    frontier: list[tuple[str, dt.datetime, str]] = [
        (key, fault_at, f"reads {origin}.{column}") for key in sorted(first_wave)
    ]

    while frontier:
        frontier.sort(key=lambda item: (item[1], item[0]))
        key, upstream_broke_at, reason = frontier.pop(0)
        if key == origin or not graph.has(key):
            continue

        when = (
            upstream_broke_at
            if _breaks_on_read(graph, key)
            else _next_refresh(graph.asset(key).refresh_cadence, upstream_broke_at)
        )
        if key in broken_at and broken_at[key] <= when:
            continue

        broken_at[key] = when
        reasons[key] = reason
        for downstream in graph.downstream(key):
            frontier.append((downstream, when, f"reads {key}"))

    events = tuple(
        sorted(Event(at=when - fault_at, key=key, reason=reasons[key]) for key, when in broken_at.items())
    )
    return Timeline(
        scenario=scenario.name,
        origin=origin,
        events=events,
        direct=tuple(sorted(first_wave)),
    )
