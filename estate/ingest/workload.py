"""Execute the consumer workload and publish the resulting usage statistics.

Twin weights fragility by how heavily an asset is used, which means the estate needs usage
data. There are two ways to produce it and only one of them is honest.

The dishonest way is to write plausible-looking query counts straight into DataHub. The
counts would be indistinguishable from real ones in the UI, and every fragility score
derived from them would be built on a number nobody measured.

This module does the other thing. It reads the workload declared in
``queries/workload.yml``, actually runs every query against the warehouse the stated
number of times, and publishes usage statistics that are counts of executions that really
happened. If a query fails, its executions are not counted, and the failure is reported.

The workload is synthetic — it stands in for BI tools and analysts this demo estate does
not have — but the execution is real, and so are the counts, the latencies and the row
counts. See queries/workload.yml for what is being claimed and what is not.

The queries themselves are reused by Stage 4: verifying a predicted failure means running
the real dashboard-backing queries against the shadow environment and observing which of
them genuinely break.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import psycopg
import yaml
from datahub.emitter.mce_builder import make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    DatasetUsageStatisticsClass,
    DatasetUserUsageCountsClass,
    TimeWindowSizeClass,
    CalendarIntervalClass,
)

QUERY_DIR = Path(__file__).parent / "queries"
WORKLOAD_FILE = QUERY_DIR / "workload.yml"

# Matches the estate's fixed anchor date, so usage windows line up with the data.
ESTATE_END = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)

WAREHOUSE_DB = os.environ.get("WAREHOUSE_DB", "warehouse")


@dataclass
class QuerySpec:
    file: str
    dataset: str
    consumer: str
    daily_runs: int
    users: list[str]
    sql: str


@dataclass
class Execution:
    """What actually happened when the workload ran."""

    runs: int = 0
    failures: int = 0
    total_seconds: float = 0.0
    rows_returned: int = 0
    error: str | None = None


def load_workload(scale: float) -> tuple[list[QuerySpec], int]:
    raw = yaml.safe_load(WORKLOAD_FILE.read_text())
    specs = []
    for entry in raw["queries"]:
        sql = (QUERY_DIR / entry["file"]).read_text()
        runs = max(1, round(entry["daily_runs"] * scale))
        specs.append(
            QuerySpec(
                file=entry["file"],
                dataset=entry["dataset"],
                consumer=entry["consumer"],
                daily_runs=runs,
                users=list(entry["users"]),
                sql=sql,
            )
        )
    return specs, int(raw["days"])


def run_workload(conn: psycopg.Connection, specs: Sequence[QuerySpec], days: int) -> dict[tuple[str, int], Execution]:
    """Execute every query, for every simulated day, and record what happened.

    Keyed by (dataset, day_offset) so that usage is published per day rather than as one
    undifferentiated total — Twin's trend analysis needs a daily grain.
    """
    results: dict[tuple[str, int], Execution] = {}
    for day_offset in range(days):
        for spec in specs:
            key = (spec.dataset, day_offset)
            execution = results.setdefault(key, Execution())
            for _ in range(spec.daily_runs):
                started = time.monotonic()
                try:
                    with conn.cursor() as cur:
                        cur.execute(spec.sql)
                        rows = cur.fetchall()
                    execution.rows_returned += len(rows)
                    execution.runs += 1
                except psycopg.Error as exc:
                    execution.failures += 1
                    execution.error = str(exc).strip().splitlines()[0]
                    conn.rollback()
                finally:
                    execution.total_seconds += time.monotonic() - started
    return results


def usage_mcps(
    specs: Sequence[QuerySpec], results: dict[tuple[str, int], Execution], days: int
) -> list[MetadataChangeProposalWrapper]:
    """Turn recorded executions into DataHub usage aspects.

    Only successful executions are counted. A query that failed did not use the dataset,
    and inflating the count with failures would overstate exactly the assets that are
    already broken.
    """
    users_by_dataset: dict[str, list[str]] = {}
    queries_by_dataset: dict[str, list[str]] = {}
    for spec in specs:
        users = users_by_dataset.setdefault(spec.dataset, [])
        for user in spec.users:
            if user not in users:
                users.append(user)
        queries_by_dataset.setdefault(spec.dataset, []).append(spec.sql.strip())

    mcps = []
    for (dataset, day_offset), execution in sorted(results.items()):
        if execution.runs == 0:
            continue
        day_start = (ESTATE_END - dt.timedelta(days=days - day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        users = users_by_dataset[dataset]
        # Distribute the day's executions across the declared users. The split is even
        # because the workload does not model per-user weighting; what matters downstream
        # is the distinct-consumer count, not who ran what.
        per_user, remainder = divmod(execution.runs, len(users))
        user_counts = [
            DatasetUserUsageCountsClass(
                user=f"urn:li:corpuser:{user}",
                count=per_user + (1 if index < remainder else 0),
            )
            for index, user in enumerate(users)
        ]

        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=make_dataset_urn(
                    platform="postgres", name=f"{WAREHOUSE_DB}.{dataset}", env="PROD"
                ),
                aspect=DatasetUsageStatisticsClass(
                    timestampMillis=int(day_start.timestamp() * 1000),
                    eventGranularity=TimeWindowSizeClass(unit=CalendarIntervalClass.DAY),
                    totalSqlQueries=execution.runs,
                    uniqueUserCount=len(users),
                    userCounts=user_counts,
                    topSqlQueries=sorted(queries_by_dataset[dataset])[:5],
                ),
            )
        )
    return mcps


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Multiplier on every query's daily run count. Use a smaller value for a faster run.",
    )
    parser.add_argument("--gms", default=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080"))
    parser.add_argument(
        "--dsn",
        default=None,
        help="Warehouse connection string. Defaults to the read-only twin_reader role.",
    )
    args = parser.parse_args(argv)

    # Deliberately the read-only role. The workload is a consumer; it has no business
    # holding write privileges, and running it this way proves the role actually works.
    dsn = args.dsn or (
        f"host={os.environ.get('WAREHOUSE_HOST', 'warehouse')} "
        f"port={os.environ.get('WAREHOUSE_PORT', '5432')} "
        f"dbname={os.environ.get('WAREHOUSE_DB', 'warehouse')} "
        f"user=twin_reader password=twin_reader"
    )

    specs, days = load_workload(args.scale)
    planned = sum(s.daily_runs for s in specs) * days
    print(f"running consumer workload: {len(specs)} queries x {days} days = {planned:,} executions")

    started = time.monotonic()
    with psycopg.connect(dsn, autocommit=True) as conn:
        results = run_workload(conn, specs, days)
    elapsed = time.monotonic() - started

    total_runs = sum(e.runs for e in results.values())
    total_failures = sum(e.failures for e in results.values())
    print(f"executed {total_runs:,} queries in {elapsed:.1f}s ({total_failures} failed)")

    if total_failures:
        for (dataset, day), execution in sorted(results.items()):
            if execution.error and day == 0:
                print(f"  FAILED {dataset}: {execution.error}")

    mcps = usage_mcps(specs, results, days)
    emitter = DatahubRestEmitter(gms_server=args.gms, token=os.environ.get("DATAHUB_GMS_TOKEN") or None)
    emitter.test_connection()
    for mcp in mcps:
        emitter.emit(mcp)

    by_dataset: dict[str, int] = {}
    for (dataset, _), execution in results.items():
        by_dataset[dataset] = by_dataset.get(dataset, 0) + execution.runs
    print(f"published {len(mcps)} usage aspects across {len(by_dataset)} datasets")
    for dataset, count in sorted(by_dataset.items(), key=lambda kv: -kv[1]):
        print(f"  {dataset:<44} {count:>7,} queries")

    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
