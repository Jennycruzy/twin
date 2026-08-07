"""Run one scenario end to end: ``make run SCENARIO=scenarios/<name>.yml``.

Read the estate, propagate the declared fault across it, execute
that fault for real in a shadow warehouse, and grade the prediction against what broke.

The report is written to be checkable rather than impressive. Every predicted event carries
the reason it was predicted, every observed failure carries the error the warehouse returned,
and the scope being scored is stated before the score is.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Iterable

from twin.faults import DEGRADED, UNAVAILABLE
from twin.read import read_estate
from twin.read.cache import load_latest, store
from twin.read.model import EstateGraph
from twin.simulate import Scenario, ScenarioError, Timeline, load_scenario, predict
from twin.simulate.paging import build as build_paging, describe_load
from twin.verify.consumers import ConsumerCheck, run_consumer_queries
from twin.verify.dbt_runner import BuildOutcome, probe_model, rebuild_downstream
from twin.verify.grade import Scorecard, grade
from twin.verify.guard import UnsafeStatement
from twin.verify.observe import AssetObservation, affected, classify, with_impact
from twin.verify.shadow import (
    ShadowEstate,
    create_passthrough,
    drop_relation,
    model_name,
    shadow_estate,
)
from twin.verify.warehouse import Credentials, ShadowConnection
from twin.target import TwinTarget, load_target

SHADOW_ARTIFACTS = Path("/tmp/twin-shadow")

_RULE = "  " + "-" * 74

_IMPACT_QUESTION = {
    UNAVAILABLE: "which assets could not be produced at all",
    DEGRADED: "which assets were produced and are wrong — the quiet failure",
}


def _load_graph(target: TwinTarget, refresh: bool) -> EstateGraph:
    """The cached graph, or a fresh read if there is none.

    Verification is run repeatedly against one state of the estate — a scenario at a time, a
    sweep over every asset — so re-reading the catalog for each would add minutes per run to
    produce a graph that is identical by construction.
    """
    if not refresh:
        cached = load_latest(target.cache_dir)
        if cached is not None:
            return cached
    graph = asyncio.run(read_estate(scope=target.catalog))
    store(graph, target.cache_dir)
    return graph


def _probe_each(
    graph: EstateGraph,
    layout: ShadowEstate,
    connection: ShadowConnection,
    artifacts: Path,
    dry_run: bool,
    target: TwinTarget,
) -> dict[str, AssetObservation]:
    """Build each downstream model on its own against the faulted asset.

    Everything the model reads is healthy except the faulted asset, so whatever happens to it
    happens because of the fault. Each result is classified the same way the cascade is —
    missing, or present and different from production — because a fault that degrades rather
    than breaks would otherwise look like a clean build.

    A model is restored to a passthrough afterwards whatever happened, so one probe cannot
    contaminate the next.
    """
    observations: dict[str, AssetObservation] = {}
    for key in layout.to_rebuild:
        outcome = probe_model(
            graph,
            layout,
            target.dbt_project,
            artifacts,
            key,
            dry_run=dry_run,
            dbt_target=target.dbt_target,
            source_env_vars=tuple(target.source_env_vars),
        )
        if not dry_run:
            observations.update(classify(connection, layout, outcome, (key,)))
            create_passthrough(connection, layout, key)
    return observations


def _print_header(scenario: Scenario, graph: EstateGraph) -> None:
    print()
    print(f"  SCENARIO   {scenario.name} — {scenario.title}")
    print(f"  FAULT      {scenario.fault.describe()}, at {scenario.fault.at.strftime('%H:%M')}")
    print(f"  GRAPH      {graph.fingerprint} — {len(graph.assets)} assets, read {graph.read_at}")


def _print_timeline(timeline: Timeline, graph: EstateGraph) -> None:
    print()
    print("  PREDICTED TIMELINE")
    print(_RULE)
    if not timeline.events:
        print("    nothing downstream reads that column")
        return
    for event in timeline.events:
        asset = graph.asset(event.key)
        shape = asset.materialization or asset.kind
        print(f"    {event.offset():>9}  {event.key:<44} {shape:<8} {event.reason}")
    print(_RULE)
    print(f"    {len(timeline.events)} assets predicted to break")


def _print_execution(layout: ShadowEstate, build: BuildOutcome, statements: int) -> None:
    print()
    print("  SHADOW EXECUTION")
    print(_RULE)
    print(f"    schema        {layout.schema}")
    print(f"    fault         {layout.faulted}.{layout.dropped_column} removed from the shadow copy")
    print(f"    passthrough   {len(layout.passthrough)} views onto the real estate")
    print(f"    rebuilt       {len(layout.to_rebuild)} models, dbt exit {build.returncode}")
    print(f"    statements    {statements} issued, every one inside {layout.schema}")


def _print_scorecard(
    title: str,
    question: str,
    card: Scorecard,
    observations: dict[str, AssetObservation],
) -> None:
    print()
    print(f"  {title}")
    print(_RULE)
    print(f"    {question}")
    print(f"    scope: {len(card.scope)} models")
    print()

    detail = {k: o.detail for k, o in observations.items()}
    for key in card.hits:
        print(f"    hit          {key:<44} {detail.get(key, '')[:58]}")
    for key in card.misses:
        print(f"    MISS         {key:<44} {detail.get(key, '')[:58]}")
    for key in card.false_alarms:
        print(f"    false alarm  {key:<44} {detail.get(key, 'built cleanly')[:58]}")

    precision = f"{card.precision:.2f}" if card.precision is not None else "n/a"
    recall = f"{card.recall:.2f}" if card.recall is not None else "n/a"
    print()
    print(
        f"    predicted {len(card.predicted)}   observed {len(card.observed)}   "
        f"precision {precision}   recall {recall}"
    )
    # The control statistic. Every comparison here is against production, so a check that
    # reported "different" for everything would manufacture perfect scores. This is the count
    # that shows it does not: models rebuilt during the same run that came out byte-for-byte
    # identical to production, each of which was an opportunity to raise a false alarm.
    identical = sum(1 for key in card.scope if observations.get(key) and not observations[key].affected)
    if identical:
        print(f"    {identical} model(s) in scope came out identical to production")
    if card.misses:
        print(f"    {len(card.misses)} miss(es) named above — broke without being predicted")
    if card.is_suspiciously_perfect:
        print("    note: a perfect score on one scenario is weak evidence, not strong")


def _print_ungraded(card: Scorecard, graph: EstateGraph) -> None:
    if not card.ungraded_predictions:
        return
    print()
    print("  PREDICTED BUT NOT GRADED")
    print(_RULE)
    print("    a dbt build cannot observe these, so they are counted nowhere above")
    kinds: dict[str, int] = {}
    for key in card.ungraded_predictions:
        kind = graph.asset(key).kind
        kinds[kind] = kinds.get(kind, 0) + 1
    for kind, count in sorted(kinds.items()):
        print(f"    {kind:<20} {count}")


def _print_paging(graph: EstateGraph, timeline: Timeline) -> None:
    paging = build_paging(graph, timeline)
    print()
    print("  WHO GETS PAGED")
    print(_RULE)
    print(f"    {describe_load(paging)}")
    print()
    for page in paging.pages:
        print(
            f"    {page.at:>9}  {page.owner:<34} {page.count:>2} asset(s), first "
            f"{page.first_asset}"
        )
    if paging.unowned:
        print()
        print(f"    pages nobody — {len(paging.unowned)} asset(s) with no owner:")
        for key in paging.unowned:
            print(f"      {key}")


def _print_consumers(checks: Iterable[ConsumerCheck]) -> None:
    checks = tuple(checks)
    broken = [c for c in checks if c.broke]
    print()
    print("  CONSUMER QUERIES")
    print(_RULE)
    print(f"    {len(checks)} real queries re-run against the shadow estate, {len(broken)} failed")
    for check in broken:
        print(f"    failed       {check.query:<28} {check.consumer:<24} {check.daily_runs:>4}/day")
        print(f"                 {check.error[:70]}")
    print()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one scenario through prediction and verification.")
    parser.add_argument("scenario", type=Path, help="path to a scenario YAML file")
    parser.add_argument("--target", help="estate target name from targets/<name>.yml")
    parser.add_argument("--refresh", action="store_true", help="re-read the estate before running")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print every statement the run would execute, without executing any",
    )
    parser.add_argument(
        "--incidents",
        action="store_true",
        help="raise a DataHub incident for every asset this run observed failing",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="append this real run to the deterministic context-integrity campaign record",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    target = load_target(args.target)

    try:
        scenario = load_scenario(args.scenario)
    except (ScenarioError, OSError) as exc:
        print(f"cannot load scenario: {exc}", file=sys.stderr)
        return 2

    graph = _load_graph(target, args.refresh)
    if not graph.has(scenario.fault.asset):
        print(f"{scenario.fault.asset} is not in the estate graph", file=sys.stderr)
        return 2

    _print_header(scenario, graph)
    timeline = predict(graph, scenario)
    _print_timeline(timeline, graph)

    artifacts = SHADOW_ARTIFACTS / target.name / scenario.name
    connection = ShadowConnection(
        schema=f"{target.shadow_prefix}{target.name}_{scenario.name}",
        credentials=Credentials.shadow_role(),
        dry_run=args.dry_run,
    )

    try:
        with connection:
            with shadow_estate(
                graph,
                scenario,
                connection,
                source_layers=tuple(sorted(target.source_layers)),
                shadow_prefix=f"{target.shadow_prefix}{target.name}_",
            ) as layout:
                probes = _probe_each(graph, layout, connection, artifacts, args.dry_run, target)
                for key in layout.to_rebuild:
                    drop_relation(connection, layout, model_name(key))
                build = rebuild_downstream(
                    graph,
                    layout,
                    target.dbt_project,
                    artifacts,
                    dry_run=args.dry_run,
                    source_layers=tuple(sorted(target.source_layers)),
                    dbt_target=target.dbt_target,
                    source_env_vars=tuple(target.source_env_vars),
                )
                observed = (
                    {}
                    if args.dry_run
                    else classify(connection, layout, build, layout.to_rebuild)
                )
                consumers = run_consumer_queries(
                    connection,
                    layout,
                    target.workload,
                    tuple(sorted(target.model_schemas)),
                )
    except UnsafeStatement as exc:
        print(f"\n  execution boundary refused a statement: {exc}\n", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - a failed run reports rather than half-succeeds
        print(f"\n  verification failed: {exc}\n", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n  STATEMENTS THIS RUN WOULD EXECUTE")
        print(_RULE)
        for statement in connection.issued:
            print(f"    {statement}")
        print()
        return 0

    _print_execution(layout, build, len(connection.issued))

    expected = scenario.fault.definition.impact
    direct = grade(timeline.direct, with_impact(probes, expected), layout.to_rebuild)
    _print_scorecard(
        f"IS IT {expected.upper()} ON ITS OWN?",
        "each model built alone, everything else healthy — the falsifiable test",
        direct,
        probes,
    )

    for impact in (UNAVAILABLE, DEGRADED):
        predicted = timeline.with_impact(impact)
        seen = with_impact(observed, impact)
        if not predicted and not seen:
            continue
        _print_scorecard(
            f"AFTER A FULL REFRESH: {impact.upper()}",
            _IMPACT_QUESTION[impact],
            grade(predicted, seen, layout.to_rebuild),
            observed,
        )

    _print_ungraded(grade(timeline.broken, affected(observed), layout.to_rebuild), graph)
    _print_paging(graph, timeline)
    _print_consumers(consumers)

    if args.evidence:
        from twin.context import record_evidence

        record_evidence(
            args.evidence,
            target.name,
            graph,
            scenario,
            timeline.broken,
            observed,
            sum(1 for check in consumers if check.broke),
        )
        print(f"  campaign evidence appended to {args.evidence}")

    if args.incidents:
        _raise_incidents(graph, scenario, observed)
    return 0


def _raise_incidents(graph, scenario, observed) -> None:
    """Raise DataHub incidents for what this run *observed*, never for what it predicted.

    Kept behind a flag rather than run by default. Writing to a catalog is a side effect on
    something the operator owns, and a verification run should not change the estate's
    metadata because somebody wanted to see a scorecard.
    """
    from twin import provenance
    from twin.write.catalog import Catalog, WriteBackError
    from twin.write.incidents import raise_for

    def urns_for(key: str) -> tuple[str, ...]:
        if not graph.has(key):
            return ()
        return tuple(u for u in graph.asset(key).urns if u.startswith("urn:li:dataset:"))

    stamp = provenance.stamp()
    line = f"graph {graph.fingerprint}; commit {stamp['commit'] or 'unknown'}"

    try:
        raised = raise_for(Catalog.connect(), scenario.name, observed, urns_for, line)
    except WriteBackError as exc:
        print(f"\n  could not raise incidents: {exc}\n", file=sys.stderr)
        return

    print("\n  INCIDENTS RAISED IN DATAHUB")
    print(_RULE)
    if not raised:
        print("    none — nothing was observed to fail, so nothing is claimed")
    for incident in raised:
        print(f"    {incident.key:<44} {incident.impact}")
    print(_RULE)
    print(f"    {len(raised)} raised against observed failures; resolve with: make unwrite\n")


if __name__ == "__main__":
    sys.exit(main())
