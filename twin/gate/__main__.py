"""Run the checks that make a Twin change safe to review and repeat."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from twin.read.model import Asset, Edge, EstateGraph
from twin.score.fragility import Weights, score_estate
from twin.score.knockout import sweep
from twin.simulate.scenario import ScenarioError, load_scenario
from twin.target import TwinTarget, load_target


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Check:
    name: str
    detail: str


class GateFailure(RuntimeError):
    """A repository invariant failed."""


def _targets() -> tuple[TwinTarget, ...]:
    return tuple(
        load_target(path.stem, ROOT / "targets")
        for path in sorted((ROOT / "targets").glob("*.yml"))
    )


def _validate_target(target: TwinTarget) -> Check:
    paths = (
        target.dbt_project,
        target.workload,
        target.scenario_dir,
        target.postgres_recipe,
        target.dbt_recipe,
    )
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise GateFailure(f"{target.name}: missing configured paths: {', '.join(missing)}")

    modules = (
        target.seed_module,
        target.metadata_module,
        target.workload_module,
        target.verify_module,
    )
    missing_modules = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing_modules:
        raise GateFailure(f"{target.name}: missing configured modules: {', '.join(missing_modules)}")

    source_file = target.dbt_project / "models" / "sources.yml"
    if not source_file.exists():
        raise GateFailure(f"{target.name}: source declarations missing at {source_file}")
    source_text = source_file.read_text()
    undeclared = [name for name in target.source_env_vars if name not in source_text]
    if undeclared:
        raise GateFailure(
            f"{target.name}: source environment variables not declared in sources.yml: "
            f"{', '.join(undeclared)}"
        )

    scenarios = sorted(target.scenario_dir.glob("*.yml"))
    if not scenarios:
        raise GateFailure(f"{target.name}: no scenarios in {target.scenario_dir}")
    names: set[str] = set()
    for path in scenarios:
        try:
            scenario = load_scenario(path)
        except (OSError, ScenarioError) as exc:
            raise GateFailure(f"{target.name}: invalid scenario {path}: {exc}") from exc
        if scenario.name in names:
            raise GateFailure(f"{target.name}: duplicate scenario name {scenario.name!r}")
        names.add(scenario.name)
    return Check(target.name, f"{len(scenarios)} scenarios, {len(target.source_layers)} source layers")


def _determinism_check() -> Check:
    graph = EstateGraph(
        assets=(
            Asset(
                key="raw.input", kind="dataset", name="input", urns=("input",), layer="raw",
                owners=("platform@example.com",), team="platform", refresh_cadence="daily",
                sla_hours=6, criticality_tier="tier1", replicated=False,
            ),
            Asset(
                key="marts.output", kind="dataset", name="output", urns=("output",), layer="marts",
                owners=("analytics@example.com",), team="analytics", refresh_cadence="daily",
                sla_hours=8, criticality_tier="tier2", replicated=False,
            ),
        ),
        edges=(Edge("raw.input", "marts.output"),),
        column_edges=(),
        read_at="fixed",
        source="gate",
    )
    weights = Weights.load(ROOT / "config/scoring.yml")
    first = tuple((score.key, score.score, score.components) for score in score_estate(graph, sweep(graph), {}, weights))
    second = tuple((score.key, score.score, score.components) for score in score_estate(graph, sweep(graph), {}, weights))
    if first != second:
        raise GateFailure("scoring is not deterministic for the fixed gate graph")
    round_tripped = EstateGraph.from_dict(json.loads(graph.to_json()))
    if graph.to_json() != round_tripped.to_json():
        raise GateFailure("graph serialization is not deterministic")
    return Check("determinism", "fixed graph produces byte-identical scores")


def _repository_check() -> Check:
    for command in (("git", "diff", "--check"), ("git", "diff", "--cached", "--check")):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        if result.returncode:
            raise GateFailure(result.stdout or result.stderr or "git diff --check failed")

    tracked = subprocess.run(
        ("git", "ls-files", "-z"), cwd=ROOT, capture_output=True, check=True
    ).stdout.decode().split("\0")
    forbidden = tuple(
        path for path in tracked
        if path and ("__pycache__/" in path or path.endswith(".pyc") or "/target/" in path or "/logs/" in path)
    )
    if forbidden:
        raise GateFailure(f"generated artifacts are tracked: {', '.join(forbidden)}")
    return Check("repository", "no whitespace errors or generated artifacts are tracked")


def _run_tests() -> Check:
    result = subprocess.run(
        (sys.executable, "-m", "pytest"), cwd=ROOT, check=False
    )
    if result.returncode:
        raise GateFailure(f"pytest exited with {result.returncode}")
    return Check("pytest", "test suite passed")


def run(skip_tests: bool = False) -> tuple[Check, ...]:
    if not list((ROOT / "targets").glob("*.yml")):
        raise GateFailure("no target configurations found")
    checks = [_repository_check(), _determinism_check()]
    checks.extend(_validate_target(target) for target in _targets())
    if not skip_tests:
        checks.append(_run_tests())
    return tuple(checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true", help="run structural checks only")
    args = parser.parse_args(argv)
    try:
        checks = run(skip_tests=args.skip_tests)
    except (GateFailure, OSError, ValueError) as exc:
        print(f"gate failed: {exc}", file=sys.stderr)
        return 1

    for check in checks:
        print(f"  PASS  {check.name:<14} {check.detail}")
    print(f"gate passed: {len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
