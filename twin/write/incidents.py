"""Raising the failures Stage 4 proved as DataHub incidents.

A fragility score is a prediction about what would happen. An incident is a statement that
something *did* happen, and Twin is in the unusual position of being able to make that
statement honestly: Stage 4 executed the fault against a real warehouse, rebuilt the
downstream models for real, and recorded PostgreSQL's own error text for every asset that
broke. The incidents raised here carry that error text verbatim.

That distinction governs what may be raised. An asset Twin *predicted* would break gets no
incident — a catalog full of incidents for things that did not happen is worse than an empty
one, and it would undo the only thing that makes Twin's output worth reading. Only observed
failures qualify, which means only assets inside the verified scope, which means the incident
list is always a subset of what the scorecard graded.

Every incident is tagged in its description with the scenario that produced it and the
provenance of the run, because an incident with no stated cause is an alert nobody can act
on. They are raised as ``ACTIVE`` and resolved — not deleted — by ``resolve_all``, since an
incident that vanishes leaves no record that the condition ever existed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    IncidentInfoClass,
    IncidentSourceClass,
    IncidentSourceTypeClass,
    IncidentStateClass,
    IncidentStatusClass,
    IncidentTypeClass,
)

from twin.faults import DEGRADED, UNAVAILABLE
from twin.verify.observe import AssetObservation
from twin.write.catalog import Catalog, WriteBackError

# Incidents Twin raises are identified by this prefix in their URN, so that resolving them
# never touches an incident somebody else raised. Same reasoning as the `twin_` property
# namespace and the `twin_shadow_` schema prefix: a rule checkable against the world.
URN_PREFIX = "urn:li:incident:twin_"

# How a proven failure maps onto DataHub's incident vocabulary. The mapping is deliberately
# conservative: where Twin's evidence does not distinguish a schema fault from a value fault,
# it says OPERATIONAL rather than guessing a more specific type that a reader would trust.
_TYPE_BY_IMPACT = {
    UNAVAILABLE: IncidentTypeClass.OPERATIONAL,
    DEGRADED: IncidentTypeClass.FIELD,
}


@dataclass(frozen=True)
class RaisedIncident:
    """One incident Twin raised, and the asset it was raised against."""

    urn: str
    entity_urn: str
    key: str
    impact: str


def incident_urn(scenario: str, key: str) -> str:
    """Deterministic, so re-running a scenario updates its incident instead of duplicating it.

    A random id per run would leave a judge who ran `make run` twice looking at two incidents
    describing one failure, which misrepresents the estate in the direction of alarm.
    """
    return f"{URN_PREFIX}{scenario}_{key}".replace(".", "_")


def raise_for(
    catalog: Catalog,
    scenario: str,
    observations: dict[str, AssetObservation],
    urns_for: object,
    provenance: str,
) -> tuple[RaisedIncident, ...]:
    """Raise an incident for every asset Stage 4 *observed* failing.

    ``urns_for`` maps a logical key to the dataset URNs it folds. Assets with no URN are
    skipped rather than invented — an incident needs something to attach to. An incident is
    attached to every URN the asset folds, usually the warehouse table and its dbt sibling,
    so it is visible whichever of the two a person opens.
    """
    raised = []
    now = int(time.time() * 1000)

    for key, observation in sorted(observations.items()):
        if not observation.affected:
            continue
        entity_urns = urns_for(key)  # type: ignore[operator]
        if not entity_urns:
            continue
        urn = incident_urn(scenario, key)

        info = IncidentInfoClass(
            type=_TYPE_BY_IMPACT.get(observation.impact or "", IncidentTypeClass.OPERATIONAL),
            entities=list(entity_urns),
            title=f"{key} {observation.impact} after {scenario}",
            description=(
                f"Twin executed the fault declared in `{scenario}` against a shadow copy of "
                f"the estate, rebuilt the downstream models with dbt, and observed this "
                f"asset {observation.impact}.\n\n"
                f"Evidence, as reported by the warehouse:\n\n    {observation.detail}\n\n"
                f"This is an observed failure, not a prediction: the fault was executed and "
                f"this is what happened. Run provenance: {provenance}"
            ),
            status=IncidentStatusClass(
                state=IncidentStateClass.ACTIVE,
                lastUpdated=_audit(now),
            ),
            source=IncidentSourceClass(type=IncidentSourceTypeClass.MANUAL),
            created=_audit(now),
            startedAt=now,
        )
        catalog._emit(urn, info)
        raised.append(
            RaisedIncident(
                urn=urn, entity_urn=entity_urns[0], key=key, impact=str(observation.impact)
            )
        )
    return tuple(raised)


def resolve_all(catalog: Catalog, urns: tuple[str, ...]) -> int:
    """Mark every incident Twin raised as resolved. Returns how many were changed.

    Resolved rather than deleted. The condition was real when it was recorded, and a catalog
    that forgets its incidents cannot be used to argue about how often anything breaks.
    """
    resolved = 0
    now = int(time.time() * 1000)
    for urn in urns:
        if not urn.startswith(URN_PREFIX):
            continue
        existing = catalog.graph.get_aspect(urn, IncidentInfoClass)
        if existing is None or existing.status.state == IncidentStateClass.RESOLVED:
            continue
        existing.status = IncidentStatusClass(
            state=IncidentStateClass.RESOLVED,
            lastUpdated=_audit(now),
            message="Resolved by `make unwrite`: the shadow estate this was proved in is gone.",
        )
        catalog._emit(urn, existing)
        resolved += 1
    return resolved


def _audit(when: int) -> object:
    """An AuditStamp naming Twin as the actor, so provenance survives into the catalog."""
    from datahub.metadata.schema_classes import AuditStampClass

    return AuditStampClass(time=when, actor="urn:li:corpuser:twin")


def raised_urns(scenarios: tuple[str, ...], keys: tuple[str, ...]) -> tuple[str, ...]:
    """Every incident URN Twin could have raised, derived rather than remembered.

    Resolution cannot discover Twin's incidents by searching for them: an incident written by
    emitting `incidentInfo` is stored and reads back correctly by URN, but DataHub's GraphQL
    search refuses to hydrate it — the entity comes back null and the whole query fails with
    NullValueInNonNullableField. That is why incident URNs are deterministic in the first
    place, so the set can be reconstructed from the scenarios and the estate without asking
    the catalog to list them. See docs/UPSTREAM.md.
    """
    return tuple(incident_urn(scenario, key) for scenario in scenarios for key in keys)


__all__ = [
    "RaisedIncident",
    "URN_PREFIX",
    "incident_urn",
    "raise_for",
    "raised_urns",
    "resolve_all",
    "WriteBackError",
]
