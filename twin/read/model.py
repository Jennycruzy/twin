"""The materialised estate graph — Twin's internal picture of the platform.

Stage 1 produces exactly one thing: an :class:`EstateGraph`. Every later stage consumes it
and nothing else in the pipeline talks to the catalog, so simulation and scoring can be
exercised against a graph loaded from disk with no DataHub instance anywhere near them.

Two properties of this model are load-bearing rather than stylistic.

**Everything is frozen and ordered.** Twin's scoring must be byte-identical across runs.
Set iteration order and dict insertion order are the classic ways that quietly stops being
true, so collections here are tuples, sorted at construction.

**Assets are logical, not physical.** DataHub holds a Postgres entity and a dbt entity for
the same table, correctly — they are different things in a catalog. In a failure model they
are one thing: dropping a column breaks one asset, not two. The two URNs are folded into a
single asset that remembers both, and lineage is expressed between logical keys.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable

# Entity kinds Twin models. Datasets carry the failure semantics; the rest are consumers
# that inherit failure from upstream and matter because of who notices when they break.
KIND_DATASET = "dataset"
KIND_DASHBOARD = "dashboard"
KIND_CHART = "chart"
KIND_ML_FEATURE_TABLE = "mlFeatureTable"
KIND_ML_FEATURE = "mlFeature"
KIND_ML_MODEL = "mlModel"
KIND_ML_MODEL_DEPLOYMENT = "mlModelDeployment"


@dataclass(frozen=True, order=True)
class Column:
    """A column, as the catalog reports it.

    ``nullable`` comes from the warehouse rather than from a dbt test, so it describes what
    the database will permit rather than what the project intends.
    """

    name: str
    native_type: str
    nullable: bool


@dataclass(frozen=True)
class Asset:
    """One logical asset, folded from every catalog entity that describes it.

    The operational metadata — cadence, SLA, tier, replication, fallback — is what makes a
    propagation model possible at all. Twin does not measure any of it; it is declared in
    the dbt project and read back here, which is stated plainly in the README's Limitations
    because a reader would otherwise reasonably assume it came from run history.
    """

    key: str
    kind: str
    name: str
    urns: tuple[str, ...]
    layer: str

    owners: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    columns: tuple[Column, ...] = ()

    team: str | None = None
    refresh_cadence: str | None = None
    sla_hours: int | None = None
    criticality_tier: str | None = None
    replicated: bool | None = None
    fallback_source: str | None = None
    materialization: str | None = None
    sub_type: str | None = None

    @property
    def is_owned(self) -> bool:
        return bool(self.owners)


@dataclass(frozen=True, order=True)
class Edge:
    """A table-grain dependency: ``target`` reads from ``source``."""

    source: str
    target: str


@dataclass(frozen=True, order=True)
class ColumnEdge:
    """A column-grain dependency: ``target.target_column`` derives from ``source.source_column``.

    Twin needs this to answer the question a table-grain graph cannot. Dropping a column
    should break the assets that read *that column*, not everything downstream of the table.
    In this estate the difference is large and load-bearing: ``stg_fx_rates.rate`` has three
    direct consumers while ``stg_fx_rates.rate_date`` has none, and a table-grain model would
    score both as identical whole-table failures.

    ``target_column`` costs real time to obtain and is worth it. DataHub's MCP server answers
    column lineage with the downstream *datasets* that consume a column, not the downstream
    fields, so each landing column is resolved by asking ``get_lineage_paths_between`` whether
    a specific pair is connected. That is a call per candidate pair, and it is what lets
    damage be followed at column grain past the first hop: knowing that a null in
    ``stg_orders.merchant_id`` corrupts ``int_orders_enriched.merchant_id`` and nothing else
    is the difference between predicting fifteen damaged assets and predicting the ten that
    really are.
    """

    source: str
    source_column: str
    target: str
    target_column: str


@dataclass(frozen=True)
class EstateGraph:
    """The whole estate, as read at one moment.

    ``read_at`` and ``source`` describe the read, not the estate, and are deliberately
    excluded from the fingerprint: the same platform read twice must produce the same
    fingerprint, or caching and the nightly trend both become meaningless.
    """

    assets: tuple[Asset, ...]
    edges: tuple[Edge, ...]
    column_edges: tuple[ColumnEdge, ...]
    read_at: str
    source: str

    unresolved_columns: int = 0
    """Column pairs the catalog could not answer for during this read.

    Recorded because those pairs were assumed connected, which widens a predicted blast
    radius. Excluded from the fingerprint: it describes the read, not the estate.
    """

    _by_key: dict[str, Asset] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "assets", tuple(sorted(self.assets, key=lambda a: a.key)))
        object.__setattr__(self, "edges", tuple(sorted(set(self.edges))))
        object.__setattr__(self, "column_edges", tuple(sorted(set(self.column_edges))))
        object.__setattr__(self, "_by_key", {a.key: a for a in self.assets})

    # ---------------------------------------------------------------- traversal

    def asset(self, key: str) -> Asset:
        return self._by_key[key]

    def has(self, key: str) -> bool:
        return key in self._by_key

    def of_kind(self, kind: str) -> tuple[Asset, ...]:
        return tuple(a for a in self.assets if a.kind == kind)

    def downstream(self, key: str) -> tuple[str, ...]:
        """Direct consumers of ``key``."""
        return tuple(sorted({e.target for e in self.edges if e.source == key}))

    def upstream(self, key: str) -> tuple[str, ...]:
        """Direct dependencies of ``key``."""
        return tuple(sorted({e.source for e in self.edges if e.target == key}))

    def reachable_downstream(self, key: str) -> tuple[str, ...]:
        """Every asset reachable from ``key``, at any depth.

        Breadth-first with an explicit seen-set: the estate's lineage is acyclic today, but
        a cycle introduced upstream must not turn a nightly run into an infinite loop.
        """
        seen: set[str] = set()
        frontier = [key]
        while frontier:
            current = frontier.pop()
            for nxt in self.downstream(current):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return tuple(sorted(seen))

    def columns_consuming(self, key: str, column: str) -> tuple[ColumnEdge, ...]:
        """Column-grain consumers of one column of one asset."""
        return tuple(e for e in self.column_edges if e.source == key and e.source_column == column)

    # ---------------------------------------------------------------- identity

    @property
    def fingerprint(self) -> str:
        """A stable hash of the estate's structure and metadata.

        Keyed on content only. Two reads of an unchanged platform agree; any change to an
        asset, an edge, a column edge or a piece of operational metadata produces a new
        fingerprint, which is what makes it safe to reuse a cached graph.
        """
        payload = json.dumps(self._structure(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _structure(self) -> dict[str, Any]:
        return {
            "assets": [asdict(a) for a in self.assets],
            "edges": [asdict(e) for e in self.edges],
            "column_edges": [asdict(e) for e in self.column_edges],
        }

    # ---------------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "read_at": self.read_at,
            "source": self.source,
            "unresolved_columns": self.unresolved_columns,
            **self._structure(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EstateGraph":
        assets = tuple(
            replace(
                Asset(**{k: v for k, v in a.items() if k != "columns"}),
                columns=tuple(Column(**c) for c in a.get("columns", ())),
                urns=tuple(a["urns"]),
                owners=tuple(a.get("owners", ())),
                tags=tuple(a.get("tags", ())),
            )
            for a in payload["assets"]
        )
        return cls(
            assets=assets,
            edges=tuple(Edge(**e) for e in payload["edges"]),
            column_edges=tuple(ColumnEdge(**e) for e in payload["column_edges"]),
            read_at=payload["read_at"],
            source=payload["source"],
            unresolved_columns=int(payload.get("unresolved_columns", 0)),
        )

    def to_json(self) -> str:
        """Canonical JSON. Sorted keys and fixed separators, so a cached graph written by
        two different runs is byte-identical and diffable."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    # ---------------------------------------------------------------- summary

    def summary_line(self) -> str:
        datasets = len(self.of_kind(KIND_DATASET))
        consumers = len(self.assets) - datasets
        unowned = sum(1 for a in self.assets if a.kind == KIND_DATASET and not a.is_owned)
        return (
            f"estate {self.fingerprint}: {len(self.assets)} assets "
            f"({datasets} datasets, {consumers} consumers), {len(self.edges)} edges, "
            f"{len(self.column_edges)} column edges, {unowned} unowned"
        )


def layer_of(key: str, kind: str) -> str:
    """Group an asset into the layer its failure behaviour belongs to.

    Datasets are grouped by warehouse schema, which in this estate is also the modelling
    layer. Everything else is grouped by what it is, because a dashboard's failure
    behaviour has nothing to do with where it sits in a database.
    """
    if kind != KIND_DATASET:
        return {
            KIND_DASHBOARD: "bi",
            KIND_CHART: "bi",
            KIND_ML_FEATURE_TABLE: "ml",
            KIND_ML_FEATURE: "ml",
            KIND_ML_MODEL: "ml",
            KIND_ML_MODEL_DEPLOYMENT: "ml",
        }.get(kind, "other")
    return key.split(".", 1)[0] if "." in key else "other"


def sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))
