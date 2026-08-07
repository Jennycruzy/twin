"""The fault vocabulary: what can go wrong, and what kind of wrong it is.

Every fault kind is declared once, here, and referenced by three modules that must agree
about it — the scenario loader that validates a file, the propagation model that predicts
its consequences, and the execution layer that runs it against a real warehouse. Declaring
it in three places is how a simulator ends up predicting something it cannot execute.

The distinction that carries the most weight is ``impact``.

**Unavailable** means the asset cannot be produced. A build fails, a relation is missing, a
consumer's query errors. Loud, and comparatively easy to predict.

**Degraded** means the asset is produced and is wrong. Rows stop arriving, a column fills
with nulls, and every build succeeds. Nothing alarms, the dashboard still renders, and the
number on it is untrue. This is the more expensive failure in practice and the harder one to
predict, and a model that reports every fault as ``unavailable`` is caught by it — which is
the reason the two are separated rather than collapsed into "breaks".
"""

from __future__ import annotations

from dataclasses import dataclass

UNAVAILABLE = "unavailable"
DEGRADED = "degraded"

# How the first wave is located. A column-grained fault reaches only the assets that read the
# affected column; a table-grained one reaches everything that reads the asset at all.
BY_COLUMN = "column"
BY_TABLE = "table"

# Layers that ingestion lands rather than dbt building them. Verification redirects dbt's source()
# schemas into the disposable shadow schema and creates passthrough views for these tables, so
# a source fault can be executed with the same isolation as a model fault.
SOURCE_LAYERS = ("raw_pg", "raw_events")


def is_source_layer(asset: str) -> bool:
    """Whether an asset key names something ingestion lands rather than dbt builds."""
    return asset.split(".")[0] in SOURCE_LAYERS


@dataclass(frozen=True)
class FaultKind:
    """One thing that can go wrong, and the shape of its consequences."""

    name: str
    summary: str
    impact: str
    reach: str
    needs_column: bool
    column_role: str = "a column"

    @property
    def is_column_grained(self) -> bool:
        return self.reach == BY_COLUMN


KINDS: dict[str, FaultKind] = {
    "drop_column": FaultKind(
        name="drop_column",
        summary="a column stops arriving",
        impact=UNAVAILABLE,
        reach=BY_COLUMN,
        needs_column=True,
    ),
    "drop_asset": FaultKind(
        name="drop_asset",
        summary="an asset is deleted outright",
        impact=UNAVAILABLE,
        reach=BY_TABLE,
        needs_column=False,
    ),
    "change_column_type": FaultKind(
        name="change_column_type",
        summary="a column's type changes incompatibly",
        impact=UNAVAILABLE,
        reach=BY_COLUMN,
        needs_column=True,
    ),
    "stop_new_rows": FaultKind(
        name="stop_new_rows",
        # Nothing fails. Every downstream model builds, and every one of them is quietly
        # short of the most recent data — which is why this fault is degraded rather than
        # unavailable, and why predicting it correctly is worth more than predicting a drop.
        summary="new rows stop arriving and the asset goes stale",
        impact=DEGRADED,
        reach=BY_TABLE,
        needs_column=True,
        column_role="the date column that stops advancing",
    ),
    "null_out_column": FaultKind(
        name="null_out_column",
        summary="a column's values become null",
        impact=DEGRADED,
        reach=BY_COLUMN,
        needs_column=True,
    ),
}


def kind(name: str) -> FaultKind:
    """Look up a fault kind, or raise ``KeyError`` naming what exists."""
    try:
        return KINDS[name]
    except KeyError:
        raise KeyError(f"unknown fault kind {name!r}; known kinds: {', '.join(sorted(KINDS))}") from None


def cascade_impact(upstream_impact: str) -> str:
    """What an asset experiences when something it reads has failed.

    An asset that cannot be produced takes everything beneath it with it. An asset that is
    merely wrong passes the wrongness on: its consumers build perfectly well and are also
    wrong. Degradation never becomes unavailability further downstream, which is exactly what
    makes a stale pipeline so expensive to notice.
    """
    return upstream_impact
