"""The fragility dimension Twin adds to the catalog.

This is the module that turns Twin from a tool that reads DataHub into one that changes it.
Everything before Stage 5 produces a ranking that lives in this repository; a ranking in a
repository is a report, and reports are read by the person who ran them. A structured
property on the asset is read by whoever opens the asset, and by any agent that asks the
catalog what it knows — which is the claim the project is actually making.

**Written through the SDK, not MCP, and this is the second documented exception.** The MCP
server exposes six tools — ``search``, ``get_lineage``, ``get_dataset_queries``,
``get_entities``, ``list_schema_fields``, ``get_lineage_paths_between`` — and every one of
them reads. There is no write tool, so an agent cannot contribute to the graph over the
interface it consumes the graph through. That is a genuine finding about the interface rather
than an inconvenience Twin worked around, and it sits alongside the usage-statistics gap in
:mod:`twin.score.usage`.

The values are read back *over MCP*, which is the half that matters. Twin verified before
building this that ``get_entities`` returns ``structuredProperties`` with the assigned value,
so a score Twin writes is visible through exactly the interface another agent would use to
find it. Writing somewhere only Twin can see would prove nothing.

**Every property is derived from something measured.** The score and its components come from
the knockout sweep, the blast radius is the count of assets that sweep actually lost, and the
bus factor is the number of distinct owners the paging model named. Nothing here is a
judgement Twin brought with it, and each definition carries the rule it was computed by in
its own description, so an asset's score can be disputed on its parts in the catalog itself
rather than only in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass

from twin.score.fragility import COMPONENTS, Score

# The namespace every property Twin writes lives under. `make unwrite` removes exactly the
# properties matching this prefix and nothing else, so the prefix is what makes the write-back
# reversible — the same reasoning as the shadow-schema prefix in the execution guard.
PREFIX = "twin_"

NUMBER = "urn:li:dataType:datahub.number"
STRING = "urn:li:dataType:datahub.string"
DATASET_ENTITY = "urn:li:entityType:datahub.dataset"


@dataclass(frozen=True)
class PropertyDefinition:
    """One structured property Twin defines in DataHub before it can write values."""

    id: str
    display_name: str
    value_type: str
    description: str

    @property
    def urn(self) -> str:
        return f"urn:li:structuredProperty:{self.id}"


def _component_definition(name: str) -> PropertyDefinition:
    return PropertyDefinition(
        id=f"{PREFIX}component_{name}",
        display_name=f"Twin: {name} component",
        value_type=NUMBER,
        description=(
            f"The {name} component of the fragility score, before weighting, as a share of a "
            "fixed denominator. Published because a score nobody can take apart is a score "
            "nobody should act on: the ranking can be disputed on this number rather than "
            "accepted or rejected whole. Weights are in config/scoring.yml."
        ),
    )


DEFINITIONS: tuple[PropertyDefinition, ...] = (
    PropertyDefinition(
        id=f"{PREFIX}fragility_score",
        display_name="Twin: fragility score",
        value_type=NUMBER,
        description=(
            "How dangerous this asset is to lose, 0-100, higher being worse. Computed by "
            "knocking the asset out in simulation and measuring what the estate loses: blast "
            "radius, recorded query exposure, available recovery paths, ownership "
            "concentration and detection blindness. Every input is measured from the catalog "
            "and the warehouse; none is a judgement about this estate. Written by Twin."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}resilience_score",
        display_name="Twin: resilience score",
        value_type=NUMBER,
        description=(
            "100 minus the fragility score, so that higher means safer. Published alongside "
            "the fragility score rather than instead of it because the two orderings are both "
            "in common use and a reader who assumes the wrong direction draws exactly the "
            "wrong conclusion. This carries no information the fragility score does not."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}fragility_rank",
        display_name="Twin: fragility rank",
        value_type=NUMBER,
        description=(
            "Where this asset sits in the estate's fragility ranking, 1 being the most "
            "fragile. Rank moves when the estate changes even if the score does not, so it is "
            "published beside the score rather than derived from it after the fact."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}blast_radius",
        display_name="Twin: blast radius",
        value_type=NUMBER,
        description=(
            "How many datasets and consumers become unavailable or degraded when this asset "
            "is lost, counted by simulating its deletion and following the damage through "
            "column-grain lineage. Not a count of graph descendants: an asset downstream that "
            "does not read the damaged columns is not in the radius."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}bus_factor",
        display_name="Twin: bus factor",
        value_type=NUMBER,
        description=(
            "How many distinct people would be paged to repair this asset's blast radius. A "
            "bus factor of 1 means one person's absence extends the outage; 0 means nothing "
            "in the radius has a declared owner at all, which is worse and is why unowned "
            "assets are counted rather than skipped."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}is_spof",
        display_name="Twin: single point of failure",
        value_type=STRING,
        description=(
            "yes when losing this asset takes out something a consumer actually reads AND at "
            "most one person can repair the result. That is the rule, stated so it can be "
            "argued with: it combines a measured blast radius that reaches a dashboard or "
            "chart with a measured bus factor of one or zero."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}scored_at",
        display_name="Twin: scored at",
        value_type=STRING,
        description=(
            "When the read this score was computed from was taken. A fragility score with no "
            "timestamp cannot be told apart from a stale one."
        ),
    ),
    PropertyDefinition(
        id=f"{PREFIX}scoring_provenance",
        display_name="Twin: scoring provenance",
        value_type=STRING,
        description=(
            "The graph fingerprint, the commit Twin was running at, and the digest of the "
            "weights file, so a score in the catalog can be traced to the code and the "
            "configuration that produced it. A ranking that shifts is either the estate "
            "changing or the model changing, and this is what tells them apart."
        ),
    ),
    *(_component_definition(name) for name in COMPONENTS),
)

BY_ID = {definition.id: definition for definition in DEFINITIONS}


def is_spof(score: Score) -> bool:
    """Whether losing this asset is a single point of failure, by the stated rule.

    The rule is deliberately narrow and deliberately visible in the property description:
    something people read goes down, and at most one person can bring it back. A definition
    that counted any wide blast radius would mark half the estate and mean nothing.
    """
    return bool(score.knockout.consumers_lost) and bus_factor(score) <= 1


def bus_factor(score: Score) -> int:
    """Distinct owners who would be paged for this asset's blast radius."""
    return len(set(score.knockout.owners_paged))


def values_for(score: Score, rank: int, scored_at: str, provenance: str) -> dict[str, object]:
    """Every property value Twin writes for one asset, keyed by property id.

    Rounded where rounding is honest — a fragility score carries three decimals because that
    is the precision the sweep produces, and a blast radius is a count.
    """
    values: dict[str, object] = {
        f"{PREFIX}fragility_score": round(score.score, 3),
        f"{PREFIX}resilience_score": round(100.0 - score.score, 3),
        f"{PREFIX}fragility_rank": rank,
        f"{PREFIX}blast_radius": score.knockout.blast,
        f"{PREFIX}bus_factor": bus_factor(score),
        f"{PREFIX}is_spof": "yes" if is_spof(score) else "no",
        f"{PREFIX}scored_at": scored_at,
        f"{PREFIX}scoring_provenance": provenance,
    }
    for name in COMPONENTS:
        values[f"{PREFIX}component_{name}"] = round(score.components[name], 4)
    return values
