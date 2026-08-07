"""Plan or execute the next deterministic context-integrity experiment."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from twin.context import all_confidence, evidence_path, rank_candidates
from twin.read import read_estate
from twin.read.cache import load_latest, store
from twin.score.fragility import CONFIG, Weights, score_estate
from twin.score.knockout import sweep
from twin.score.usage import read_usage
from twin.simulate.scenario import ScenarioError, load_scenario
from twin.target import load_target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--execute", action="store_true", help="run the highest-ranked real scenario")
    parser.add_argument("--evidence", type=Path, default=None)
    args = parser.parse_args(argv)
    target = load_target(args.target)
    graph = None if args.refresh else load_latest(target.cache_dir)
    if graph is None:
        graph = asyncio.run(read_estate(scope=target.catalog))
        store(graph, target.cache_dir)
    usage = read_usage(scope=target.catalog)
    weights = Weights.load(CONFIG)
    scores = score_estate(graph, sweep(graph), usage, weights)
    fragility = {score.key: score.score for score in scores}
    scenarios = []
    for path in sorted(target.scenario_dir.glob("*.yml")):
        try:
            scenarios.append(load_scenario(path))
        except (OSError, ScenarioError) as exc:
            print(f"cannot load {path}: {exc}", file=sys.stderr)
            return 2
    record = args.evidence or evidence_path(target.cache_dir)
    candidates = rank_candidates(graph, scenarios, fragility, usage, record)
    if not candidates:
        print(f"no runnable scenarios for target {target.name}", file=sys.stderr)
        return 2

    print()
    print(f"  CONTEXT-INTEGRITY CAMPAIGN — {target.name}")
    print("  " + "-" * 92)
    print("  deterministic objective: business impact × context gap × verification novelty")
    print()
    print(f"  {'#':>2}  {'SCENARIO':<38} {'PRIORITY':>8} {'IMPACT':>7} {'GAP':>7} {'STATE':<8} {'FAULT'}")
    print("  " + "-" * 92)
    for rank, candidate in enumerate(candidates, 1):
        print(
            f"  {rank:>2}  {candidate.scenario.name:<38} {candidate.priority:>8.3f}"
            f" {candidate.impact:>7.3f} {candidate.context_gap:>7.3f}"
            f" {candidate.confidence.state:<8} {candidate.scenario.fault.asset}"
        )
    print("  " + "-" * 92)
    selected = candidates[0]
    print(
        f"  selected {selected.scenario.name}: impact={selected.impact:.3f}, "
        f"context confidence={selected.confidence.score:.3f}, evidence={record}"
    )
    if not args.execute:
        print("  execute it with: make campaign TARGET=" + target.name + " CAMPAIGN_EXECUTE=1")
        return 0

    print("  executing the selected scenario in the real shadow verifier")
    subprocess.run(
        [
            sys.executable, "-m", "twin.run", "--target", target.name,
            "--evidence", str(record), str(selected.scenario.path),
        ],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
