"""Observing what actually happened to each asset, and in what way.

A dbt exit code answers one question: did it build? That is enough for a fault that deletes
something, and useless for the faults that matter more. When rows stop arriving or a column
fills with nulls, every model builds, every test that only checks structure passes, and the
estate is quietly wrong. Grading a prediction of *degradation* against a build log would
score it as a false alarm every time.

So each asset in scope is classified against production rather than against an exit code:

* **unavailable** — the build failed, or the model was never attempted because something it
  reads failed. It is not there.
* **degraded** — it is there, and its contents differ from the same model built from healthy
  inputs.
* nothing — it built, and it matches production exactly.

The comparison is against the real estate, which is the only baseline that cannot be argued
with. It is a comparison of *contents* rather than of row counts, and the difference is not
academic. A daily revenue mart fed three days of missing orders has exactly the same number
of rows as production and understates three days of revenue: row counts would call that
healthy and score a correct prediction as a false alarm. Twin would look worse than it is,
which is its own kind of dishonesty.

So each relation is reduced to a row count and an order-independent checksum of every row.
Both sides are read from the same database in the same statement shape, so the comparison
answers one question exactly: is this the data production has, or not.
"""

from __future__ import annotations

from dataclasses import dataclass

from twin.faults import DEGRADED, UNAVAILABLE
from twin.verify.dbt_runner import BuildOutcome
from twin.verify.shadow import ShadowEstate, model_name
from twin.verify.warehouse import ShadowConnection, qualified


@dataclass(frozen=True)
class AssetObservation:
    """What happened to one asset, and the evidence for saying so."""

    key: str
    impact: str | None
    detail: str

    @property
    def affected(self) -> bool:
        return self.impact is not None


def _source_schema(key: str) -> str:
    return key.split(".")[0]


@dataclass(frozen=True)
class Contents:
    """A relation's row count and a checksum over every row in it."""

    rows: int
    checksum: int


def _contents(connection: ShadowConnection, schema: str, name: str) -> Contents:
    """Summarise a relation so two of them can be compared.

    The checksum sums a hash of each row, which is order-independent by construction — no
    sort is needed, and no assumption is made about the order two builds happen to produce.
    ``NULL`` for an empty relation is folded to zero so that empty and empty compare equal.
    """
    rows = connection.fetch(
        f"SELECT count(*), coalesce(sum(hashtext(t::text)::bigint), 0) "
        f"FROM {qualified(schema, name)} t"
    )
    return Contents(rows=int(rows[0][0]), checksum=int(rows[0][1]))


def classify(
    connection: ShadowConnection,
    layout: ShadowEstate,
    build: BuildOutcome,
    keys: tuple[str, ...],
) -> dict[str, AssetObservation]:
    """Classify each asset by comparing the shadow estate against production."""
    failed_tests = {r.key for r in build.failed_tests if r.key}
    observations: dict[str, AssetObservation] = {}

    for key in keys:
        name = model_name(key)
        failure = build.failure_for(key)
        if failure is not None:
            observations[key] = AssetObservation(
                key=key, impact=UNAVAILABLE, detail=failure.message or failure.status
            )
            continue

        try:
            shadow = _contents(connection, layout.schema, name)
        except Exception as exc:  # noqa: BLE001 - a missing relation is an observation
            observations[key] = AssetObservation(
                key=key, impact=UNAVAILABLE, detail=str(exc).strip().splitlines()[0]
            )
            continue

        production = _contents(connection, _source_schema(key), name)
        if shadow.rows != production.rows:
            observations[key] = AssetObservation(
                key=key,
                impact=DEGRADED,
                detail=f"{shadow.rows:,} rows against {production.rows:,} in production "
                f"({shadow.rows - production.rows:+,})",
            )
        elif shadow.checksum != production.checksum:
            # The expensive kind of wrong: the right number of rows, holding wrong values.
            observations[key] = AssetObservation(
                key=key,
                impact=DEGRADED,
                detail=f"{shadow.rows:,} rows, same count as production, contents differ",
            )
        elif key in failed_tests:
            observations[key] = AssetObservation(
                key=key, impact=DEGRADED, detail="contents match production, dbt test failed"
            )
        else:
            observations[key] = AssetObservation(
                key=key, impact=None, detail="identical to production"
            )

    return observations


def with_impact(observations: dict[str, AssetObservation], impact: str) -> tuple[str, ...]:
    return tuple(sorted(k for k, o in observations.items() if o.impact == impact))


def affected(observations: dict[str, AssetObservation]) -> tuple[str, ...]:
    return tuple(sorted(k for k, o in observations.items() if o.affected))
