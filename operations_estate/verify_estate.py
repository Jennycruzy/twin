"""Independently verify that the operations estate exists in DataHub."""

from __future__ import annotations

import os
import sys

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import DatasetUsageStatisticsClass, UpstreamLineageClass

INSTANCE = "operations"
EXPECTED_POSTGRES_DATASETS = 25
EXPECTED_DBT_DATASETS = 25


def scoped(urn: str, platform: str) -> bool:
    return f"urn:li:dataPlatform:{platform},{INSTANCE}.warehouse." in urn


def main() -> int:
    graph = DataHubGraph(DatahubClientConfig(server=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080")))
    postgres = sorted(u for u in graph.get_urns_by_filter(entity_types=["dataset"], platform="postgres") if scoped(u, "postgres"))
    dbt = sorted(u for u in graph.get_urns_by_filter(entity_types=["dataset"], platform="dbt") if scoped(u, "dbt"))
    dashboards = [u for u in graph.get_urns_by_filter(entity_types=["dashboard"]) if u.startswith("urn:li:dashboard:(ops-looker,")]
    charts = [u for u in graph.get_urns_by_filter(entity_types=["chart"]) if u.startswith("urn:li:chart:(ops-looker,")]
    features = [u for u in graph.get_urns_by_filter(entity_types=["mlFeature"]) if u.startswith("urn:li:mlFeature:(ops-fs,")]
    lineage = 0
    used = 0
    for urn in dbt:
        aspect = graph.get_aspect(urn, UpstreamLineageClass)
        if aspect and aspect.upstreams:
            lineage += 1
    for urn in postgres:
        buckets = graph.get_timeseries_values(
            entity_urn=urn, aspect_type=DatasetUsageStatisticsClass, filter={}, limit=90
        )
        if any(bucket.totalSqlQueries for bucket in buckets):
            used += 1
    checks = [
        ("postgres datasets", len(postgres), EXPECTED_POSTGRES_DATASETS),
        ("dbt datasets", len(dbt), EXPECTED_DBT_DATASETS),
        ("dashboards", len(dashboards), 2),
        ("charts", len(charts), 4),
        ("ml features", len(features), 4),
        ("dbt assets with lineage", lineage, 10),
        ("datasets with usage", used, 4),
    ]
    print("OPERATIONS ESTATE")
    passed = True
    for name, observed, minimum in checks:
        ok = observed >= minimum
        passed &= ok
        print(f"  {name:<28} {observed:>4}  >= {minimum:<4} {'OK' if ok else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
