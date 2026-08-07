"""Execute the operations consumer workload and publish measured usage to DataHub."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    CalendarIntervalClass, DatasetUsageStatisticsClass, DatasetUserUsageCountsClass,
    TimeWindowSizeClass,
)

QUERY_DIR = Path(__file__).parent / "queries"
WORKLOAD_FILE = QUERY_DIR / "workload.yml"
END = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
INSTANCE = "operations"
WAREHOUSE_DB = os.environ.get("WAREHOUSE_DB", "warehouse")


@dataclass
class Spec:
    dataset: str
    runs: int
    users: list[str]
    sql: str


def dataset_urn(key: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:postgres,{INSTANCE}.{WAREHOUSE_DB}.{key},PROD)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args(argv)
    raw = yaml.safe_load(WORKLOAD_FILE.read_text())
    specs = [
        Spec(q["dataset"], max(1, round(q["daily_runs"] * args.scale)), list(q["users"]),
             (QUERY_DIR / q["file"]).read_text())
        for q in raw["queries"]
    ]
    days = int(raw["days"])
    dsn = (
        f"host={os.environ.get('WAREHOUSE_HOST', 'warehouse')} "
        f"port={os.environ.get('WAREHOUSE_PORT', '5432')} dbname={WAREHOUSE_DB} "
        "user=twin_reader password=twin_reader"
    )
    results: dict[tuple[str, int], int] = {}
    failures = 0
    with psycopg.connect(dsn, autocommit=True) as conn:
        for day in range(days):
            for spec in specs:
                count = 0
                for _ in range(spec.runs):
                    try:
                        with conn.cursor() as cur:
                            cur.execute(spec.sql)
                            cur.fetchall()
                        count += 1
                    except psycopg.Error:
                        failures += 1
                        conn.rollback()
                results[(spec.dataset, day)] = count

    users = {spec.dataset: spec.users for spec in specs}
    sql_by_dataset = {spec.dataset: spec.sql.strip() for spec in specs}
    emitter = DatahubRestEmitter(
        gms_server=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080"),
        token=os.environ.get("DATAHUB_GMS_TOKEN") or None,
    )
    emitter.test_connection()
    published = 0
    for (dataset, day), count in sorted(results.items()):
        if not count:
            continue
        day_start = (END - dt.timedelta(days=days - day)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        declared = users[dataset]
        per_user, remainder = divmod(count, len(declared))
        user_counts = [
            DatasetUserUsageCountsClass(user=f"urn:li:corpuser:{user}", count=per_user + (i < remainder))
            for i, user in enumerate(declared)
        ]
        emitter.emit(
            MetadataChangeProposalWrapper(
                entityUrn=dataset_urn(dataset),
                aspect=DatasetUsageStatisticsClass(
                    timestampMillis=int(day_start.timestamp() * 1000),
                    eventGranularity=TimeWindowSizeClass(unit=CalendarIntervalClass.DAY),
                    totalSqlQueries=count, uniqueUserCount=len(declared), userCounts=user_counts,
                    topSqlQueries=[sql_by_dataset[dataset]],
                ),
            )
        )
        published += 1
    print(
        f"operations workload: {len(specs)} queries x {days} days; "
        f"published {published} usage aspects; failures={failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
