"""Read the estate out of DataHub and assemble it into a graph.

The work is four passes. Discover every entity Twin models, fetch full metadata for each,
fold catalog entities into logical assets, then resolve dependencies at table grain and
column grain. Everything is read through the MCP server; nothing here reaches for the SDK.

Two details of DataHub's model shape this code and are worth stating plainly.

**Lineage lives on the dbt entity.** Asking Postgres' ``stg_orders`` for its upstreams
returns its dbt sibling and nothing else — the sibling relationship surfaces as an edge.
Asking the dbt entity returns the real model dependencies. Both are read anyway: sibling
edges collapse to self-edges once assets are folded and are dropped, so the fold does the
disambiguation instead of a platform special case.

**Column lineage lives on the physical entity**, and points the other way. Fine-grained
lineage is emitted against the Postgres entity, so column-grain reads are issued there and
in the downstream direction, which is the direction a failure travels.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Iterable, Sequence

from twin.read.mcp_client import DataHubMCP
from twin.read.model import (
    KIND_DATASET,
    KIND_ML_FEATURE_TABLE,
    Asset,
    ColumnEdge,
    Column,
    Edge,
    EstateGraph,
    layer_of,
    sorted_unique,
)
from twin.target import CatalogScope

# Entity types Twin models, in the order they are discovered. mlModelDeployment is absent
# on purpose: it is not a searchable type through the MCP server's filter syntax and its
# properties are not returned by get_entities, so the ML branch is modelled as far as the
# model itself. The deployment is the one estate entity Twin cannot see over MCP, and the
# README says so rather than reaching around the interface to fetch it.
SEARCHABLE_KINDS = (
    "dataset",
    "dashboard",
    "chart",
    "mlFeatureTable",
    "mlFeature",
    "mlModel",
)

# The warehouse database name prefixes every dataset URN. Assets are keyed by
# schema-qualified name, which is what a person calls the table and what the dbt project
# calls it too.
_DB_PREFIX_PARTS = 1


async def materialize(
    client: DataHubMCP, source: str, scope: CatalogScope | None = None
) -> EstateGraph:
    """Read one scoped estate from DataHub and return it as a graph."""
    entities = await _discover(client, scope)
    urn_to_key = {e["urn"]: _key_for(e, scope) for e in entities}
    assets = _fold(entities, urn_to_key)

    edges = await _table_edges(client, list(urn_to_key), urn_to_key)
    edges += _containment_edges(entities, urn_to_key)
    column_edges = await _column_edges(client, assets, urn_to_key)

    return EstateGraph(
        assets=assets,
        edges=tuple(edges),
        column_edges=tuple(column_edges),
        read_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        source=source,
        unresolved_columns=len(client.unresolved),
    )


# ---------------------------------------------------------------------- discovery


async def _discover(
    client: DataHubMCP, scope: CatalogScope | None = None
) -> list[dict[str, Any]]:
    """Every entity of every modelled kind, filtered to one target after hydration."""
    found = await asyncio.gather(*(client.search(f"entity_type = {k}") for k in SEARCHABLE_KINDS))
    urns = sorted({hit["urn"] for hits in found for hit in hits})
    entities = await client.get_entities(urns)
    if scope is None:
        return entities
    return [entity for entity in entities if scope.accepts(entity)]


def _kind_of(urn: str) -> str:
    """``urn:li:dashboard:(looker,finance-revenue-review)`` -> ``dashboard``."""
    return urn.split(":", 3)[2]


def _key_for(entity: dict[str, Any], scope: CatalogScope | None = None) -> str:
    """The logical name an asset is known by.

    Datasets key on schema-qualified name so that the Postgres and dbt entities for one
    table fold together. Everything else keys on kind and name, which keeps a chart named
    like a table from colliding with it.
    """
    urn = entity["urn"]
    kind = _kind_of(urn)
    if kind == KIND_DATASET:
        qualified = urn.split("(", 1)[1].split(",")[1]
        if scope and qualified.startswith(scope.dataset_path_prefix):
            return qualified[len(scope.dataset_path_prefix):]
        return qualified.split(".", _DB_PREFIX_PARTS)[-1]
    return f"{kind}:{_display_name(entity)}"


def _display_name(entity: dict[str, Any]) -> str:
    properties = entity.get("properties") or {}
    for candidate in (entity.get("name"), properties.get("name"), entity.get("dashboardId"), entity.get("chartId")):
        if candidate:
            return str(candidate)
    return entity["urn"]


# ---------------------------------------------------------------------- folding


def _custom_properties(entity: dict[str, Any]) -> dict[str, str]:
    properties = entity.get("properties") or {}
    return {p["key"]: p["value"] for p in properties.get("customProperties") or [] if "key" in p}


def _owners(entity: dict[str, Any]) -> tuple[str, ...]:
    ownership = entity.get("ownership") or {}
    return sorted_unique(
        owner["owner"]["urn"].split(":")[-1]
        for owner in ownership.get("owners") or []
        if (owner.get("owner") or {}).get("urn")
    )


def _tags(entity: dict[str, Any]) -> tuple[str, ...]:
    tags = entity.get("tags") or {}
    return sorted_unique(
        tag["tag"]["urn"].removeprefix("urn:li:tag:")
        for tag in tags.get("tags") or []
        if (tag.get("tag") or {}).get("urn")
    )


def _columns(entity: dict[str, Any]) -> tuple[Column, ...]:
    schema = entity.get("schemaMetadata") or {}
    return tuple(
        sorted(
            Column(
                name=f["fieldPath"],
                native_type=f.get("nativeDataType") or "unknown",
                nullable=bool(f.get("nullable", True)),
            )
            for f in schema.get("fields") or []
        )
    )


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _as_bool(value: str | None) -> bool | None:
    return value.strip().lower() == "true" if isinstance(value, str) else None


def _asset_from(entity: dict[str, Any], key: str) -> Asset:
    kind = _kind_of(entity["urn"])
    props = _custom_properties(entity)
    return Asset(
        key=key,
        kind=kind,
        name=_display_name(entity),
        urns=(entity["urn"],),
        layer=layer_of(key, kind),
        owners=_owners(entity),
        tags=_tags(entity),
        columns=_columns(entity),
        team=props.get("team"),
        refresh_cadence=props.get("refresh_cadence"),
        sla_hours=_as_int(props.get("sla_hours")),
        criticality_tier=props.get("criticality_tier"),
        replicated=_as_bool(props.get("replicated")),
        fallback_source=props.get("fallback_source"),
        materialization=props.get("materialization"),
        sub_type=next(iter((entity.get("subTypes") or {}).get("typeNames") or []), None),
    )


def _merge(left: Asset, right: Asset) -> Asset:
    """Fold two catalog entities describing one logical asset.

    Neither side is authoritative for everything. The Postgres entity carries the real
    column types; the dbt entity carries ownership, tags and the operational metadata. So
    the merge takes the union of the collections and the first non-empty value of each
    scalar, with the sides visited in a fixed order so the result cannot depend on which
    search returned first.
    """
    return Asset(
        key=left.key,
        kind=left.kind,
        name=left.name,
        urns=tuple(sorted(set(left.urns) | set(right.urns))),
        layer=left.layer,
        owners=sorted_unique(left.owners + right.owners),
        tags=sorted_unique(left.tags + right.tags),
        columns=left.columns or right.columns,
        team=left.team or right.team,
        refresh_cadence=left.refresh_cadence or right.refresh_cadence,
        sla_hours=left.sla_hours if left.sla_hours is not None else right.sla_hours,
        criticality_tier=left.criticality_tier or right.criticality_tier,
        replicated=left.replicated if left.replicated is not None else right.replicated,
        fallback_source=left.fallback_source or right.fallback_source,
        materialization=left.materialization or right.materialization,
        sub_type=left.sub_type or right.sub_type,
    )


def _fold(entities: Sequence[dict[str, Any]], urn_to_key: dict[str, str]) -> tuple[Asset, ...]:
    """Collapse catalog entities into logical assets."""
    folded: dict[str, Asset] = {}
    for entity in sorted(entities, key=lambda e: e["urn"]):
        key = urn_to_key[entity["urn"]]
        asset = _asset_from(entity, key)
        folded[key] = _merge(folded[key], asset) if key in folded else asset
    return tuple(folded.values())


# ---------------------------------------------------------------------- edges


async def _table_edges(
    client: DataHubMCP, urns: Sequence[str], urn_to_key: dict[str, str]
) -> list[Edge]:
    """One hop of upstream lineage for every entity, mapped onto logical assets.

    Edges to entities Twin does not model are dropped rather than invented as placeholder
    nodes: a node with no metadata cannot be scored, and carrying it would inflate every
    blast radius by an asset nobody can act on.
    """
    results = await asyncio.gather(*(client.upstreams(urn) for urn in urns))
    edges: set[Edge] = set()
    for urn, upstreams in zip(urns, results):
        target = urn_to_key[urn]
        for upstream in upstreams:
            source = urn_to_key.get(upstream.get("urn", ""))
            if source and source != target:
                edges.add(Edge(source=source, target=target))
    return sorted(edges)


def _containment_edges(
    entities: Sequence[dict[str, Any]], urn_to_key: dict[str, str]
) -> list[Edge]:
    """Features to the feature table that groups them.

    The feature table has no lineage of its own — its features are derived from dbt models
    and the model consumes the features directly. The containment edge is what makes the
    table degrade when its features do, rather than sitting in the graph as an island.
    """
    edges: set[Edge] = set()
    for entity in entities:
        if _kind_of(entity["urn"]) != KIND_ML_FEATURE_TABLE:
            continue
        table_key = urn_to_key[entity["urn"]]
        properties = entity.get("featureTableProperties") or {}
        for feature in properties.get("mlFeatures") or []:
            feature_urn = feature.get("urn") if isinstance(feature, dict) else feature
            feature_key = urn_to_key.get(feature_urn or "")
            if feature_key and feature_key != table_key:
                edges.add(Edge(source=feature_key, target=table_key))
    return sorted(edges)


def _physical_urn(asset: Asset) -> str:
    """The URN column lineage is emitted against.

    dbt ingestion writes fine-grained lineage onto the physical entity, and asking the dbt
    sibling for column lineage returns the sibling. Preferring the non-dbt URN halves the
    number of round trips and is the difference between a column-grain read that is worth
    doing nightly and one that is not.
    """
    physical = [u for u in asset.urns if ":dataPlatform:dbt," not in u]
    return (physical or list(asset.urns))[0]


async def _column_edges(
    client: DataHubMCP, assets: Iterable[Asset], urn_to_key: dict[str, str]
) -> list[ColumnEdge]:
    """Column lineage, resolved all the way to the column it lands on.

    Two passes, because the interface answers two different questions. The first asks which
    datasets consume a column, which is cheap and gives the edges. The second asks, for each
    of those edges, which column of the consumer the value actually became — one call per
    candidate pair, which is the expensive part of a read and the reason the graph is cached.

    Without the second pass, damage can only be followed at column grain for a single hop.
    After that every consumer of a damaged asset looks equally damaged, which over-predicts
    the quiet failures — exactly the ones worth predicting well.

    Every column of the consumer is a candidate, deliberately, and an attempt to narrow that
    was reverted. The obvious narrowing is to ask each column where it comes from and skip
    the pairs whose answer does not include the producing asset. It is a third faster and it
    is wrong: DataHub answers the two directions from different entities and not always
    symmetrically, and on this estate it dropped ``stg_fx_rates.rate`` ->
    ``mart_subscription_health.avg_subscriber_lifetime_usd`` — an edge shadow execution
    proves is real, since that mart genuinely fails when the column is dropped. A read that
    silently loses edges costs more than a read that takes twelve minutes.
    """
    by_key = {asset.key: asset for asset in assets}
    probes = [
        (asset.key, _physical_urn(asset), column.name)
        for asset in by_key.values()
        if asset.kind == KIND_DATASET
        for column in asset.columns
    ]
    consumers = await asyncio.gather(
        *(client.downstreams(urn, column=column) for _, urn, column in probes)
    )

    # Candidate landings: every column of every consuming asset is a possible destination
    # until the catalog says otherwise.
    candidates: list[tuple[str, str, str, str, str, str]] = []
    for (key, source_urn, column), found in zip(probes, consumers):
        for consumer in found:
            target = urn_to_key.get(consumer.get("urn", ""))
            if not target or target == key or target not in by_key:
                continue
            target_urn = consumer["urn"]
            for landing in by_key[target].columns:
                candidates.append((key, column, target, landing.name, source_urn, target_urn))

    resolved = await asyncio.gather(
        *(
            client.column_path_exists(source_urn, target_urn, source_column, target_column)
            for _, source_column, _, target_column, source_urn, target_urn in candidates
        )
    )

    edges = {
        ColumnEdge(source=key, source_column=source_column, target=target, target_column=target_column)
        for (key, source_column, target, target_column, _, _), connected in zip(candidates, resolved)
        if connected
    }
    return sorted(edges)
