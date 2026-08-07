"""How heavily each asset is really used.

This is the one place in Twin's pipeline that does not read DataHub over MCP, and the
exception is deliberate, narrow and stated rather than hidden.

DataHub holds the estate's usage statistics — counts of query executions that genuinely
happened, published by `make estate` after running the workload for real. The MCP server
exposes no tool that returns them. `get_dataset_queries` returns query *entities* — their
text, their subjects, who last ran them — and carries no execution counts. There is no other
tool that does.

That leaves three options. Drop usage-weighted scoring, and rank a heavily-used mart the same
as an unread one. Invent counts, which is the exact dishonesty this project was built to
avoid. Or read the real numbers through the SDK and say so.

Twin takes the third and pays for it in precision of language: structure, lineage, ownership
and operational metadata come over MCP; usage counts come from the SDK, because the agent
interface cannot answer for them. The gap is a genuine finding about that interface and is
recorded in the README as one.

If DataHub later exposes usage over MCP, this module is the only thing that changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import DatasetUsageStatisticsClass
from twin.target import CatalogScope

# The workload publishes one bucket per simulated day; ninety covers the estate's history
# with room to spare.
_BUCKETS = 90


@dataclass(frozen=True)
class Usage:
    """Recorded executions against one logical asset."""

    key: str
    queries: int
    users: int


def _logical_key(urn: str, scope: CatalogScope | None = None) -> str:
    """``...,warehouse.marts.mart_revenue_daily,PROD)`` -> ``marts.mart_revenue_daily``."""
    qualified = urn.split("(", 1)[1].split(",")[1]
    if scope and qualified.startswith(scope.dataset_path_prefix):
        return qualified[len(scope.dataset_path_prefix):]
    return qualified.split(".", 1)[1]


def read_usage(
    gms_url: str | None = None, scope: CatalogScope | None = None
) -> dict[str, Usage]:
    """Query counts per logical asset, as measured, or empty if DataHub is unreachable.

    Returning empty rather than raising is deliberate: scoring must still run against a
    cached graph with no DataHub in front of it, and an asset with no recorded usage scores
    as unused rather than as an error. The report says how many assets carried usage data so
    that a silently empty read cannot pass for a lightly-used estate.
    """
    server = gms_url or os.environ.get("DATAHUB_GMS_URL") or "http://datahub-gms:8080"
    try:
        graph = DataHubGraph(
            DatahubClientConfig(server=server, token=os.environ.get("DATAHUB_GMS_TOKEN") or None)
        )
        graph.test_connection()
    except Exception:
        return {}

    usage: dict[str, Usage] = {}
    for urn in graph.get_urns_by_filter(entity_types=["dataset"], platform="postgres"):
        if scope is not None and not scope.accepts({"urn": urn}):
            continue
        buckets = graph.get_timeseries_values(
            entity_urn=urn,
            aspect_type=DatasetUsageStatisticsClass,
            filter={},
            limit=_BUCKETS,
        )
        queries = sum(b.totalSqlQueries or 0 for b in buckets)
        if not queries:
            continue
        people = {
            counter.user
            for bucket in buckets
            for counter in (bucket.userCounts or [])
            if counter.user
        }
        key = _logical_key(urn, scope)
        usage[key] = Usage(key=key, queries=queries, users=len(people))
    return usage
