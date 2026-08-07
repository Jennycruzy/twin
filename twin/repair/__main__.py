"""Generate a catalog repair proposal from the current cached graph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from twin.read.cache import load_latest
from twin.repair.proposal import RepairError, build_proposal, write_proposal
from twin.simulate.scenario import load_scenario
from twin.target import load_target


def _default_scenario(target) -> Path | None:
    for path in sorted(target.scenario_dir.glob("*.yml")):
        scenario = load_scenario(path)
        if (
            scenario.fault.column
            and scenario.fault.asset.split(".", 1)[0] in target.source_layers
        ):
            return path
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=None, help="target name from targets/")
    parser.add_argument("--scenario", type=Path, help="source-column scenario to explain")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("examples/repair-prs"),
        help="directory for the Markdown and patch artifacts",
    )
    args = parser.parse_args(argv)
    target = load_target(args.target)
    scenario_path = args.scenario or _default_scenario(target)
    if scenario_path is None:
        print(f"no column-level source scenario exists for {target.name}", file=sys.stderr)
        return 2

    graph = load_latest(target.cache_dir)
    if graph is None:
        print(
            f"no cached graph for {target.name}; run `make read TARGET={target.name}` first",
            file=sys.stderr,
        )
        return 2

    try:
        proposal = build_proposal(graph, target, load_scenario(scenario_path))
        markdown, patch = write_proposal(proposal, args.output_dir)
    except (OSError, RepairError, ValueError) as exc:
        print(f"cannot generate repair proposal: {exc}", file=sys.stderr)
        return 2

    print(f"proposal: {markdown}")
    print(f"patch:    {patch}")
    print(f"finding:  {proposal.source_key}.{proposal.column}")
    print(f"graph:    {proposal.graph_fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
