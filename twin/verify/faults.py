"""Executing a fault against the shadow estate.

Each fault kind is realised as the shadow copy of the affected asset: a relation that is
genuinely missing a column, genuinely holding the wrong type, genuinely short of recent rows.
Downstream dbt then builds against it for real, and what happens, happens.

Nothing here approximates. A staleness fault does not set a flag that Twin later reads back —
it withholds the rows, and the row counts downstream fall because the rows are not there.
That is the difference between this project and a report generator, and it is why the fault
vocabulary in ``twin.faults`` only contains kinds that can be expressed as a relation.

The statements this module produces all create relations inside the run's shadow schema, so
they pass the execution boundary. Nothing here can name anything else.
"""

from __future__ import annotations

from twin.faults import kind as fault_kind
from twin.read.model import Asset
from twin.simulate.scenario import Fault
from twin.verify.warehouse import columns_clause, literal, qualified, quote


class FaultNotExecutable(ValueError):
    """The fault cannot be expressed against this asset."""


def _require_column(asset: Asset, column: str | None) -> str:
    names = [c.name for c in asset.columns]
    if column not in names:
        raise FaultNotExecutable(
            f"{asset.key} has no column {column!r} (columns: {', '.join(names)})"
        )
    return str(column)


def _column_type(asset: Asset, column: str) -> str:
    return next(c.native_type for c in asset.columns if c.name == column)


def faulted_relation_sql(
    fault: Fault, asset: Asset, shadow_schema: str, source_schema: str, name: str
) -> str | None:
    """The statement that creates the faulted copy, or ``None`` if there should not be one.

    A deleted asset has no shadow relation at all — the honest way to execute a deletion is
    for the thing to be absent, so that everything downstream meets the same missing relation
    a real deletion would produce.
    """
    definition = fault_kind(fault.kind)
    source = qualified(source_schema, name)
    target = qualified(shadow_schema, name)
    columns = [c.name for c in asset.columns]

    if definition.name == "drop_asset":
        return None

    if definition.name == "drop_column":
        dropped = _require_column(asset, fault.column)
        surviving = [c for c in columns if c != dropped]
        if not surviving:
            raise FaultNotExecutable(f"{asset.key} has only the column being dropped")
        return f"CREATE VIEW {target} AS SELECT {columns_clause(surviving)} FROM {source}"

    if definition.name == "change_column_type":
        changed = _require_column(asset, fault.column)
        native = _column_type(asset, changed).lower()
        if "char" in native or native == "text":
            raise FaultNotExecutable(
                f"{asset.key}.{changed} is already text, so casting it to text is not a fault"
            )
        # Text is the type an upstream system regresses to when a feed loses its schema, and
        # it is the one every other type can hold, so the cast itself always succeeds here.
        # Whether anything downstream survives it is the question being asked.
        projected = ", ".join(
            f"{quote(c)}::text AS {quote(c)}" if c == changed else quote(c) for c in columns
        )
        return f"CREATE VIEW {target} AS SELECT {projected} FROM {source}"

    if definition.name == "null_out_column":
        nulled = _require_column(asset, fault.column)
        native = _column_type(asset, nulled)
        projected = ", ".join(
            f"NULL::{native} AS {quote(c)}" if c == nulled else quote(c) for c in columns
        )
        return f"CREATE VIEW {target} AS SELECT {projected} FROM {source}"

    if definition.name == "stop_new_rows":
        dated = _require_column(asset, fault.column)
        # Withhold the most recent days rather than truncating at a fixed date, so the fault
        # means the same thing whenever it is run and the scenario does not silently expire
        # as the estate's data moves.
        cutoff = (
            f"(SELECT MAX({quote(dated)}) - INTERVAL {literal(f'{fault.withhold_days} days')} "
            f"FROM {source})"
        )
        return (
            f"CREATE VIEW {target} AS SELECT {columns_clause(columns)} FROM {source} "
            f"WHERE {quote(dated)} <= {cutoff}"
        )

    raise FaultNotExecutable(f"no execution defined for fault kind {definition.name!r}")
