"""Who gets called, in what order, and who is missing.

A timeline says what fails. A paging list says what that costs a team at 04:12, which is the
form the same information has to take before anyone can act on it.

Three things decide the order, and all three come from metadata the estate already carries
rather than from anything Twin invents:

**When it fails.** The first person paged is the owner of the first asset to fail, not the
owner of the most important one — a mart that breaks at 08:00 does not wake anybody at 05:30.

**What it costs.** An asset's weight is its criticality tier, raised if consumers read it
directly. A tier-1 mart behind three dashboards outranks a tier-3 intermediate model that
nothing looks at.

**Who is missing.** Unowned assets are listed separately rather than dropped. An asset with
no owner does not page anyone, which makes it *more* dangerous than one that does, and a
report that silently omitted them would describe an incident response that cannot happen.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from twin.faults import UNAVAILABLE
from twin.read.model import KIND_DATASET, EstateGraph
from twin.simulate.propagate import Event, Timeline

# Criticality tiers, most severe first. An asset with no declared tier is treated as the
# least severe rather than the most: inventing urgency the estate never declared would put
# noise at the top of the list, which is how paging lists get ignored.
_TIER_WEIGHT = {"tier1": 3, "tier2": 2, "tier3": 1}
_UNTIERED_WEIGHT = 1

# A consumer reading an asset directly is what turns a build failure into a person noticing.
_CONSUMER_BONUS = 2


@dataclass(frozen=True)
class Page:
    """One owner, and the first thing of theirs that fails."""

    owner: str
    at: str
    first_asset: str
    impact: str
    assets: tuple[str, ...]
    severity: int

    @property
    def count(self) -> int:
        return len(self.assets)


@dataclass(frozen=True)
class PagingList:
    """Everyone the incident reaches, and everything it reaches that pages nobody."""

    pages: tuple[Page, ...]
    unowned: tuple[str, ...]

    @property
    def people(self) -> int:
        return len(self.pages)


def _severity(graph: EstateGraph, key: str) -> int:
    asset = graph.asset(key)
    weight = _TIER_WEIGHT.get((asset.criticality_tier or "").lower(), _UNTIERED_WEIGHT)
    consumers = [k for k in graph.downstream(key) if graph.asset(k).kind != KIND_DATASET]
    return weight + (_CONSUMER_BONUS if consumers else 0)


def build(
    graph: EstateGraph, timeline: Timeline, departed_owner: str | None = None
) -> PagingList:
    """Turn a timeline into the calls it would generate."""
    by_owner: dict[str, list[Event]] = collections.defaultdict(list)
    unowned: list[str] = []

    for event in timeline.events:
        if not graph.has(event.key):
            continue
        owners = tuple(
            owner for owner in graph.asset(event.key).owners if owner != departed_owner
        )
        if not owners:
            unowned.append(event.key)
            continue
        for owner in owners:
            by_owner[owner].append(event)

    pages = []
    for owner, events in by_owner.items():
        ordered = sorted(events)
        first = ordered[0]
        pages.append(
            Page(
                owner=owner,
                at=first.offset(),
                first_asset=first.key,
                impact=first.impact,
                assets=tuple(sorted({e.key for e in ordered})),
                severity=max(_severity(graph, e.key) for e in ordered),
            )
        )

    # Ordered the way the night actually happens: by when the phone rings, then by how bad it
    # is, then by name so two runs of the same estate produce the same list.
    pages.sort(key=lambda p: (p.at, -p.severity, p.owner))
    return PagingList(pages=tuple(pages), unowned=tuple(sorted(set(unowned))))


def describe_load(paging: PagingList) -> str:
    """One line on how concentrated the response is.

    Concentration is the finding, not the count. An incident that pages five people is a bad
    night; one that pages the same person for eleven assets is a bus factor with a deadline.
    """
    if not paging.pages:
        return "nobody is paged"
    heaviest = max(paging.pages, key=lambda p: p.count)
    return (
        f"{paging.people} owner(s) paged, heaviest {heaviest.owner} with {heaviest.count} asset(s)"
        + (f", {len(paging.unowned)} asset(s) page nobody" if paging.unowned else "")
    )
