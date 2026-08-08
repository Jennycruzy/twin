"""Fragility: how dangerous each asset is, and why.

The score is a weighted sum of five measured components. It is not the interesting part —
the interesting part is that every component is separately reported, so a ranking can be
disputed on its parts rather than accepted or rejected whole. A number nobody can take apart
is a number nobody should act on.

What the model is built to get right is the case where size and danger disagree. In this
estate, `raw_pg.orders` has the widest reach of anything, and it is not the most fragile
asset, because it has a standby. `raw_pg.fx_rates` reaches slightly less and has none. A
scorer that ranks by fan-out returns orders and is confidently wrong, which is precisely why
the estate was built with that trap in it and why the trap is not annotated anywhere.

Components, each measured and normalised across the estate:

* **blast** — what the knockout sweep says falls over, assets and consumers together.
* **exposure** — query executions that really happened against what falls over.
* **recovery** — whether anything can serve this if it is lost: replication and declared
  fallbacks, on the asset and on the sources beneath it.
* **concentration** — how few people own the wreckage, and how much of it owns nobody.
* **blindness** — how long the damage sits before reaching something a person looks at.

Normalisation is min-max across the estate, so a score is a position in *this* platform
rather than an absolute. Two estates' scores are not comparable, and the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from twin.read.model import KIND_DATASET, EstateGraph
from twin.score.cost import CostModel
from twin.score.knockout import Knockout
from twin.score.usage import Usage

CONFIG = Path("config/scoring.yml")

COMPONENTS = ("blast", "exposure", "recovery", "concentration", "blindness")


@dataclass(frozen=True)
class Weights:
    """The scoring model's parameters, read from config rather than compiled in."""

    weights: Mapping[str, float]
    detection_horizon_hours: float

    @classmethod
    def load(cls, path: Path = CONFIG) -> "Weights":
        payload = yaml.safe_load(path.read_text()) or {}
        weights = {name: float(payload.get("weights", {}).get(name, 0.0)) for name in COMPONENTS}
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"{path}: weights must sum to 1.0, got {total:.3f}")
        return cls(
            weights=weights,
            detection_horizon_hours=float(payload.get("detection_horizon_hours", 24)),
        )


@dataclass(frozen=True)
class Coverage:
    """How much of the estate carried the metadata each component needs.

    Published because a component with no data underneath it does not fail loudly — it goes
    flat, and the ranking quietly falls back to the components that remain. On an estate
    where nobody records replication, `recovery` contributes nothing and the model collapses
    toward fan-out, which is the answer it exists to avoid. A reader is entitled to know
    which of these numbers is resting on measurement.
    """

    replication: float
    usage: float
    ownership: float
    tiers: float

    @classmethod
    def measure(cls, graph: EstateGraph, usage: Mapping[str, Usage]) -> "Coverage":
        datasets = graph.of_kind(KIND_DATASET)
        total = float(len(datasets)) or 1.0
        return cls(
            # Counted as "could this asset's recovery be determined at all", not as "does
            # this asset declare replication". Replication is declared on the sources that
            # land data, and a model twelve hops downstream inherits it, so counting
            # declarations would report 41% coverage for a component that is actually
            # answerable everywhere — and would fire a warning about a problem that is not
            # happening. A misleading health indicator is worse than none.
            replication=sum(1 for a in datasets if _recovery_is_determined(graph, a.key)) / total,
            usage=len(usage) / total,
            ownership=sum(1 for a in datasets if a.owners) / total,
            tiers=sum(1 for a in datasets if a.criticality_tier) / total,
        )


@dataclass(frozen=True)
class Score:
    """One asset's fragility, with the parts it was built from."""

    key: str
    score: float
    components: Mapping[str, float]
    raw: Mapping[str, float]
    knockout: Knockout = field(repr=False)
    blast_radius_cost: float = 0.0

    def explain(self) -> str:
        return "  ".join(f"{name[:4]} {self.components[name]:.2f}" for name in COMPONENTS)


def _as_share(values: Mapping[str, float], total: float) -> dict[str, float]:
    """Express each value as a share of a fixed total rather than of the estate's spread.

    Min-max normalisation was the obvious choice here and is wrong for one specific reason:
    it makes a score relative to the other assets *on the night it was computed*. The largest
    asset always scores 1.00, so a fragility score rising over three weeks could mean the
    asset became more dangerous, or could mean something bigger was deleted. The nightly
    trend is built on these numbers, and a trend whose baseline moves underneath it measures
    nothing.

    A fixed denominator — the size of the estate, the total queries recorded — keeps the
    ranking identical within a run and makes two runs comparable. It also means a score of
    1.00 is unreachable in practice, which is correct: no single asset is the whole platform.
    """
    if not values or total <= 0:
        return {key: 0.0 for key in values}
    return {key: min(value / total, 1.0) for key, value in values.items()}


def _recovery_is_determined(graph: EstateGraph, key: str) -> bool:
    """Whether anything beneath this asset declares replication.

    An asset with nothing declared anywhere upstream scores the neutral 0.5, which is a
    stand-in rather than a measurement, and is what coverage is reporting on.
    """
    if graph.asset(key).replicated is not None:
        return True
    return any(graph.asset(k).replicated is not None for k in _reachable_upstream(graph, key))


def _unprotected(graph: EstateGraph, key: str) -> float:
    """How exposed this asset is to losing what it depends on.

    Replication and fallbacks are declared on the sources that land data, not on the models
    built from them, so a model inherits the exposure of everything beneath it. An asset
    resting entirely on unreplicated sources with no declared fallback scores 1.0; one whose
    sources are all replicated scores 0.0.
    """
    upstream = [k for k in _reachable_upstream(graph, key) if graph.asset(k).replicated is not None]
    sources = upstream or ([key] if graph.asset(key).replicated is not None else [])
    if not sources:
        return 0.5  # nothing declared either way: neither credited nor condemned

    exposed = 0.0
    for source in sources:
        asset = graph.asset(source)
        if asset.replicated:
            continue
        exposed += 0.5 if asset.fallback_source else 1.0
    return min(exposed / len(sources), 1.0)


def _reachable_upstream(graph: EstateGraph, key: str) -> set[str]:
    seen: set[str] = set()
    frontier = [key]
    while frontier:
        current = frontier.pop()
        for nxt in graph.upstream(current):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def _concentration(graph: EstateGraph, shot: Knockout) -> float:
    """How few people can fix the wreckage, and how much of it belongs to no one.

    Two failure modes with one consequence. An incident spread across five owners is a bad
    night; the same incident owned by one person is a bus factor, and one owned by nobody is
    worse still because there is no one to page at all.
    """
    radius = len(shot.datasets_lost) + len(shot.consumers_lost)
    if not radius:
        return 0.0

    owners = len(set(shot.owners_paged))
    unowned_share = len(shot.unowned_in_radius) / radius
    # One owner is maximum concentration; each additional owner dilutes it.
    owner_concentration = 1.0 / owners if owners else 1.0
    return min(0.5 * owner_concentration + 0.5 * unowned_share, 1.0)


def score_estate(
    graph: EstateGraph,
    knockouts: Iterable[Knockout],
    usage: Mapping[str, Usage],
    weights: Weights,
    cost_model: CostModel | None = None,
) -> tuple[Score, ...]:
    """Score every asset in the sweep, ranked most fragile first."""
    shots = {shot.key: shot for shot in knockouts}

    blast = {key: float(shot.blast) for key, shot in shots.items()}
    exposure = {
        key: float(sum(usage[k].queries for k in shot.datasets_lost if k in usage))
        for key, shot in shots.items()
    }
    recovery = {key: _unprotected(graph, key) for key in shots}
    concentration = {key: _concentration(graph, shot) for key, shot in shots.items()}

    horizon = weights.detection_horizon_hours * 3600
    blindness = {
        key: (
            min(shot.first_consumer_at.total_seconds() / horizon, 1.0)
            if shot.first_consumer_at is not None
            else 0.0
        )
        for key, shot in shots.items()
    }

    # Denominators are properties of the estate, not of the spread of scores within it, so
    # tonight's number means the same thing as last night's.
    estate_size = float(len(graph.assets))
    total_queries = float(sum(u.queries for u in usage.values()))

    normalised = {
        "blast": _as_share(blast, estate_size),
        "exposure": _as_share(exposure, total_queries),
        # Already 0..1 by construction and meaningful in absolute terms: normalising would
        # make "least bad in this estate" look safe rather than merely comparatively better.
        "recovery": recovery,
        "concentration": concentration,
        "blindness": blindness,
    }
    raw = {
        "blast": blast,
        "exposure": exposure,
        "recovery": recovery,
        "concentration": concentration,
        "blindness": blindness,
    }

    cost_model = cost_model or CostModel.load()
    scores = [
        Score(
            key=key,
            score=100.0
            * sum(weights.weights[name] * normalised[name][key] for name in COMPONENTS),
            components={name: normalised[name][key] for name in COMPONENTS},
            raw={name: raw[name][key] for name in COMPONENTS},
            knockout=shots[key],
            blast_radius_cost=cost_model.estimate(
                graph, shots[key].datasets_lost, shots[key].consumers_lost
            ),
        )
        for key in shots
    ]
    # Ranked by score, then by key so that ties resolve identically on every run.
    return tuple(sorted(scores, key=lambda s: (-s.score, s.key)))
