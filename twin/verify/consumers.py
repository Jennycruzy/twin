"""Re-run the estate's real consumer queries against the broken shadow estate.

A rebuilt warehouse is only half of what a failure means. The other half is whether the
things people actually look at still answer — and those are the queries in
``estate/ingest/queries/``, the same ones the workload executes on every ``make estate`` and
the same ones DataHub's usage statistics were counted from. Nothing here is written for the
demonstration; the queries existed before Stage 4 did.

Each query is re-pointed at the shadow schema and run as ``twin_shadow``, which can read but
cannot write. A query that fails does so because the relation it wants is missing or a
column it selects no longer exists, and the error recorded is PostgreSQL's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from twin.verify.shadow import ShadowEstate
from twin.verify.warehouse import ShadowConnection

# The estate's model layers. A consumer query names them explicitly, so pointing a query at
# the shadow estate means rewriting exactly these prefixes and nothing else. Source layers
# are absent on purpose: raw data is never copied, so a query reading it should keep reading
# the real thing.
_MODEL_SCHEMAS = ("staging", "intermediate", "marts", "ml")
_SCHEMA_REFERENCE = re.compile(rf"\b({'|'.join(_MODEL_SCHEMAS)})\.", re.IGNORECASE)


@dataclass(frozen=True)
class ConsumerCheck:
    """One consumer query, run against the shadow estate."""

    query: str
    dataset: str
    consumer: str
    daily_runs: int
    error: str | None

    @property
    def broke(self) -> bool:
        return self.error is not None


def repoint(sql: str, schema: str) -> str:
    """Rewrite a consumer query to read the shadow estate."""
    return _SCHEMA_REFERENCE.sub(f"{schema}.", sql)


def load_workload(workload_path: Path) -> tuple[dict, ...]:
    payload = yaml.safe_load(workload_path.read_text()) or {}
    return tuple(payload.get("queries") or ())


def run_consumer_queries(
    connection: ShadowConnection, layout: ShadowEstate, workload_path: Path
) -> tuple[ConsumerCheck, ...]:
    """Run every declared consumer query against the shadow estate."""
    queries_dir = workload_path.parent
    checks = []
    for declared in load_workload(workload_path):
        sql = (queries_dir / declared["file"]).read_text()
        failure = connection.try_execute(repoint(sql, layout.schema))
        checks.append(
            ConsumerCheck(
                query=str(declared["file"]),
                dataset=str(declared.get("dataset", "")),
                consumer=str(declared.get("consumer", "")),
                daily_runs=int(declared.get("daily_runs", 0)),
                error=failure.error if failure else None,
            )
        )
    return tuple(sorted(checks, key=lambda c: c.query))
