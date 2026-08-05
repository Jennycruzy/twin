"""Prove the demo estate is real.

This is the check a reader runs instead of taking the README's word for it. It queries
DataHub over the wire and asserts the estate has the structure Twin needs — the right
entities, real lineage at table and column grain, a resolvable path from the ML feature
layer to a live deployment, and an ownership distribution with the intended skew.

It prints a table and exits non-zero if any check fails. There is nothing to configure and
nothing cached: every number below is read from DataHub at the moment the check runs.

Note on which client this uses: Twin's pipeline reads DataHub exclusively through the
official MCP server. This script deliberately does not. It is not part of the pipeline —
it is the independent check that the pipeline has something valid to read, and using the
same access path for both would mean a bug in that path could hide itself.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import time
from dataclasses import dataclass
from typing import Iterable

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import (
    DatasetUsageStatisticsClass,
    MLModelPropertiesClass,
    MLFeatureTablePropertiesClass,
    OwnershipClass,
    UpstreamLineageClass,
)

# The estate's intended shape. These are the numbers `make estate` builds, and a mismatch
# means the build is incomplete rather than that the threshold is wrong.
EXPECTED_DATASETS = 66
EXPECTED_DASHBOARDS = 4
EXPECTED_CHARTS = 10
EXPECTED_ML_FEATURES = 9
MIN_LINEAGE_EDGES = 65
MIN_CLL_DATASETS = 35
MIN_USED_DATASETS = 10
MIN_OWNERS = 5

# The bus-factor finding depends on one person owning a large minority of the estate. The
# range is deliberately a range: the check is that the skew exists, not that it is a
# precise value that would break every time a model is added.
TOP_OWNER_SHARE_RANGE = (0.25, 0.40)
MIN_UNOWNED_DATASETS = 5


@dataclass
class Check:
    name: str
    observed: str
    expected: str
    passed: bool


class EstateInspector:
    """Reads the estate once, then answers questions about it."""

    def __init__(self, graph: DataHubGraph) -> None:
        self.graph = graph
        self.postgres = sorted(graph.get_urns_by_filter(entity_types=["dataset"], platform="postgres"))
        self.dbt = sorted(graph.get_urns_by_filter(entity_types=["dataset"], platform="dbt"))
        self.dashboards = sorted(graph.get_urns_by_filter(entity_types=["dashboard"]))
        self.charts = sorted(graph.get_urns_by_filter(entity_types=["chart"]))

    @staticmethod
    def logical_name(urn: str) -> str:
        """Collapse a dataset URN to the logical asset it names.

        dbt and Postgres entities for the same table are siblings in DataHub, and lineage
        crosses freely between them. Twin reasons about logical assets, so both URNs
        resolve to one name here.
        """
        return urn.split(",")[1].split(".", 1)[1]

    def lineage(self) -> tuple[dict[str, set[str]], int, int]:
        """Return downstream adjacency, table edge count, and column-lineage dataset count.

        Counted in logical assets, not URNs. A model carries column lineage on both its
        dbt and its Postgres entity, and counting URNs would report the estate as having
        twice the column-level coverage it actually has.
        """
        downstream: dict[str, set[str]] = collections.defaultdict(set)
        with_cll: set[str] = set()
        for urn in self.postgres + self.dbt:
            aspect = self.graph.get_aspect(urn, UpstreamLineageClass)
            if aspect is None:
                continue
            if aspect.fineGrainedLineages:
                with_cll.add(self.logical_name(urn))
            for upstream in aspect.upstreams or []:
                source = self.logical_name(upstream.dataset)
                target = self.logical_name(urn)
                if source != target:
                    downstream[source].add(target)
        edges = sum(len(v) for v in downstream.values())
        return downstream, edges, len(with_cll)

    def ownership(self) -> tuple[collections.Counter, int]:
        """Owner -> count of logical datasets owned, plus the unowned count.

        Ownership is declared on the dbt entity but the estate is counted in logical
        assets, so both sibling URNs are folded into one name before counting. Without
        that fold, every owned asset would be counted twice.
        """
        owned_names: dict[str, set[str]] = collections.defaultdict(set)
        all_names: set[str] = set()

        for urn in self.postgres + self.dbt:
            name = self.logical_name(urn)
            all_names.add(name)
            aspect = self.graph.get_aspect(urn, OwnershipClass)
            for owner in aspect.owners if aspect else []:
                owned_names[owner.owner.split(":")[-1]].add(name)

        owners = collections.Counter({o: len(names) for o, names in owned_names.items()})
        has_owner = set().union(*owned_names.values()) if owned_names else set()
        return owners, len(all_names - has_owner)

    def usage(self) -> tuple[int, int]:
        """Datasets with usage statistics, and total recorded query executions.

        Usage is a timeseries aspect, so it is fetched per time bucket rather than as a
        single current value. The total below is the sum across every published day, which
        is the number of query executions the workload runner actually performed.
        """
        used = 0
        total = 0
        for urn in self.postgres:
            buckets = self.graph.get_timeseries_values(
                entity_urn=urn,
                aspect_type=DatasetUsageStatisticsClass,
                filter={},
                limit=90,
            )
            queries = sum(b.totalSqlQueries or 0 for b in buckets)
            if queries:
                used += 1
                total += queries
        return used, total

    def ml_path(self) -> list[str]:
        """Resolve feature table -> features -> model -> deployment, or return what broke."""
        tables = list(self.graph.get_urns_by_filter(entity_types=["mlFeatureTable"]))
        models = list(self.graph.get_urns_by_filter(entity_types=["mlModel"]))
        if not tables or not models:
            return []
        table_props = self.graph.get_aspect(tables[0], MLFeatureTablePropertiesClass)
        model_props = self.graph.get_aspect(models[0], MLModelPropertiesClass)
        if not table_props or not model_props:
            return []
        if not (model_props.mlFeatures and model_props.deployments):
            return []
        shared = set(table_props.mlFeatures or []) & set(model_props.mlFeatures or [])
        if not shared:
            return []
        return [tables[0], sorted(shared)[0], models[0], model_props.deployments[0]]




def run_checks(
    inspector: EstateInspector,
) -> tuple[list[Check], collections.Counter, int, dict, list[str]]:
    downstream, edges, cll_datasets = inspector.lineage()
    owners, unowned = inspector.ownership()
    used_datasets, total_queries = inspector.usage()
    ml_path = inspector.ml_path()

    logical = {inspector.logical_name(u) for u in inspector.postgres}
    top_owner, top_count = owners.most_common(1)[0] if owners else ("none", 0)
    top_share = top_count / len(logical) if logical else 0.0

    features = list(inspector.graph.get_urns_by_filter(entity_types=["mlFeature"]))

    checks = [
        Check("Datasets (postgres)", str(len(inspector.postgres)), f"= {EXPECTED_DATASETS}",
              len(inspector.postgres) == EXPECTED_DATASETS),
        Check("Datasets (dbt siblings)", str(len(inspector.dbt)), f"= {EXPECTED_DATASETS}",
              len(inspector.dbt) == EXPECTED_DATASETS),
        Check("Dashboards", str(len(inspector.dashboards)), f"= {EXPECTED_DASHBOARDS}",
              len(inspector.dashboards) == EXPECTED_DASHBOARDS),
        Check("Charts", str(len(inspector.charts)), f"= {EXPECTED_CHARTS}",
              len(inspector.charts) == EXPECTED_CHARTS),
        Check("ML features", str(len(features)), f"= {EXPECTED_ML_FEATURES}",
              len(features) == EXPECTED_ML_FEATURES),
        Check("Table lineage edges", str(edges), f">= {MIN_LINEAGE_EDGES}", edges >= MIN_LINEAGE_EDGES),
        Check("Datasets with column lineage", str(cll_datasets), f">= {MIN_CLL_DATASETS}",
              cll_datasets >= MIN_CLL_DATASETS),
        Check("Datasets with usage stats", str(used_datasets), f">= {MIN_USED_DATASETS}",
              used_datasets >= MIN_USED_DATASETS),
        Check("Recorded query executions", f"{total_queries:,}", "> 0", total_queries > 0),
        Check("Distinct owners", str(len(owners)), f">= {MIN_OWNERS}", len(owners) >= MIN_OWNERS),
        Check("Top owner concentration", f"{top_owner} {top_share:.0%}",
              f"{TOP_OWNER_SHARE_RANGE[0]:.0%}-{TOP_OWNER_SHARE_RANGE[1]:.0%}",
              TOP_OWNER_SHARE_RANGE[0] <= top_share <= TOP_OWNER_SHARE_RANGE[1]),
        Check("Unowned datasets", str(unowned), f">= {MIN_UNOWNED_DATASETS}",
              unowned >= MIN_UNOWNED_DATASETS),
        Check("ML path featuretable->deployment", "resolved" if ml_path else "broken",
              "resolved", bool(ml_path)),
    ]
    return checks, owners, unowned, downstream, ml_path


def print_report(
    checks: list[Check],
    owners: collections.Counter,
    unowned: int,
    estate_size: int,
    downstream: dict,
    ml_path: list[str],
) -> None:
    width = max(len(c.name) for c in checks)
    print()
    print("  ESTATE VERIFICATION")
    print("  " + "-" * (width + 42))
    print(f"  {'CHECK'.ljust(width)}  {'OBSERVED':<30} {'EXPECTED':<12} ")
    print("  " + "-" * (width + 42))
    for check in checks:
        mark = "ok  " if check.passed else "FAIL"
        print(f"  {check.name.ljust(width)}  {check.observed:<30} {check.expected:<12} {mark}")
    print("  " + "-" * (width + 42))

    print("\n  Ownership distribution")
    # Shares are of the whole estate, not of the owned subset, so they agree with the
    # concentration check above and so the unowned share is visible rather than implied.
    for owner, count in owners.most_common():
        bar = "#" * round(40 * count / estate_size)
        print(f"    {owner:<34} {count:>3} ({count / estate_size:>4.0%})  {bar}")
    if unowned:
        bar = "#" * round(40 * unowned / estate_size)
        print(f"    {'(no owner)':<34} {unowned:>3} ({unowned / estate_size:>4.0%})  {bar}")

    if ml_path:
        print("\n  ML lineage path")
        for step in ml_path:
            label = step.split(":")[3].split(",")[0] if step.count(":") > 3 else step
            print(f"    -> {step}")

    fan_out = sorted(((len(v), k) for k, v in downstream.items()), reverse=True)[:5]
    print("\n  Widest direct fan-out")
    for count, name in fan_out:
        print(f"    {name:<44} {count} direct downstream")
    print()


def settle(graph: DataHubGraph, timeout: float, interval: float) -> tuple:
    """Read the estate repeatedly until the catalog stops changing.

    DataHub indexes asynchronously through OpenSearch, so ingestion returns before the
    entities it wrote are searchable. Checked immediately after `make estate`, this script
    reported 41 of 66 dbt siblings and failed — with nothing actually wrong. On a first run
    that failure is the first thing a reader sees, and it says the estate is broken when it
    is merely young.

    So the wait is for *stability*, not for success. Polling stops the moment two consecutive
    reads agree, and the verdict is then whatever those stable numbers say. An estate that is
    genuinely wrong still fails, and fails immediately, because wrong numbers are stable
    numbers. Retrying until the checks pass would have been the easier fix and would have
    made this script incapable of reporting a real problem.
    """
    deadline = time.monotonic() + timeout
    previous: tuple[str, ...] | None = None

    while True:
        inspector = EstateInspector(graph)
        result = run_checks(inspector)
        checks = result[0]
        observed = tuple(c.observed for c in checks)

        if all(c.passed for c in checks) or observed == previous or time.monotonic() >= deadline:
            return (inspector, *result)

        passing = sum(1 for c in checks if c.passed)
        print(
            f"  waiting for the catalog to settle — {passing}/{len(checks)} checks passing",
            file=sys.stderr,
        )
        previous = observed
        time.sleep(interval)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gms", default=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080"))
    parser.add_argument(
        "--settle-timeout",
        type=float,
        default=180.0,
        help="how long to allow for asynchronous indexing before judging the estate",
    )
    parser.add_argument("--settle-interval", type=float, default=10.0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        graph = DataHubGraph(
            DatahubClientConfig(server=args.gms, token=os.environ.get("DATAHUB_GMS_TOKEN") or None)
        )
        graph.test_connection()
    except Exception as exc:
        print(f"cannot reach DataHub at {args.gms}: {exc}", file=sys.stderr)
        print("is the stack up? try `make up`.", file=sys.stderr)
        return 2

    inspector, checks, owners, unowned, downstream, ml_path = settle(
        graph, args.settle_timeout, args.settle_interval
    )
    print_report(checks, owners, unowned, len(inspector.postgres), downstream, ml_path)

    failed = [c for c in checks if not c.passed]
    if failed:
        print(f"  {len(failed)} check(s) failed: {', '.join(c.name for c in failed)}\n", file=sys.stderr)
        return 1
    print(f"  all {len(checks)} checks passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
