"""Evaluate changed assets against Twin's cached fragility graph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

from twin import provenance
from twin.read.cache import load_latest
from twin.score.cost import CostModel
from twin.score.fragility import CONFIG, Weights, score_estate
from twin.score.knockout import sweep
from twin.score.usage import read_usage
from twin.target import load_target


class PRGateError(ValueError):
    """The PR manifest or cached graph cannot be evaluated."""


def _manifest(path: Path) -> tuple[str | None, float, tuple[str, ...]]:
    try:
        payload: Any = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PRGateError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PRGateError(f"{path} must contain a mapping")
    assets = payload.get("changed_assets", payload.get("assets"))
    if not isinstance(assets, list) or not assets or not all(isinstance(item, str) for item in assets):
        raise PRGateError(f"{path} must list changed_assets")
    try:
        threshold = float(payload.get("high_fragility_threshold", payload.get("threshold", 50.0)))
    except (TypeError, ValueError) as exc:
        raise PRGateError(f"{path}: threshold must be numeric") from exc
    if threshold < 0:
        raise PRGateError(f"{path}: threshold cannot be negative")
    return (
        str(payload["target"]) if payload.get("target") is not None else None,
        threshold,
        tuple(dict.fromkeys(assets)),
    )


def evaluate(manifest: Path, target_name: str | None = None) -> tuple[str, bool]:
    manifest_target, threshold, changed = _manifest(manifest)
    target = load_target(target_name or manifest_target)
    graph = load_latest(target.cache_dir)
    if graph is None:
        raise PRGateError(f"no cached graph for {target.name}; run `make read TARGET={target.name}`")
    missing = [key for key in changed if not graph.has(key)]
    if missing:
        raise PRGateError(f"manifest assets are absent from {graph.fingerprint}: {', '.join(missing)}")

    cost_model = CostModel.load()
    scores = score_estate(
        graph,
        sweep(graph),
        read_usage(scope=target.catalog),
        Weights.load(CONFIG),
        cost_model,
    )
    by_key = {score.key: score for score in scores}
    rank = {score.key: index for index, score in enumerate(scores, start=1)}
    risky = [key for key in changed if by_key[key].score >= threshold]
    status = "FAIL" if risky else "PASS"
    stamp = provenance.stamp()
    lines = [
        "## Twin PR risk gate",
        "",
        f"**{status}** — changed assets were re-scored against cached graph `{graph.fingerprint}`.",
        "",
        f"- Manifest: `{manifest.as_posix()}`",
        f"- Target: `{target.name}`",
        f"- Threshold: `{threshold:g}` (high fragility is score ≥ threshold)",
        f"- Provenance: commit `{stamp['commit'] or 'unknown'}`"
        + (" (dirty working tree)" if stamp["dirty"] else ""),
        "- Run provenance: generated locally by `make pr-gate`",
        "",
        "| Changed asset | Rank | Fragility | Blast radius | Cost | Gate |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for key in changed:
        score = by_key[key]
        verdict = "FAIL" if key in risky else "pass"
        lines.append(
            f"| `{key}` | {rank[key]} | {score.score:.3f} | {score.knockout.blast} | "
            f"${score.blast_radius_cost:,.2f} | {verdict} |"
        )
    lines.extend(
        [
            "",
            f"Cost is illustrative, {cost_model.assumptions_line()}.",
            "",
            "This gate reads the manifest and the repository's cached graph; it does not claim a CI run or a live catalog read.",
            "",
        ]
    )
    return "\n".join(lines), bool(risky)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        body, failed = evaluate(args.manifest, args.target)
    except (OSError, PRGateError, ValueError) as exc:
        print(f"pr-gate failed: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body)
        print(f"comment body: {args.output}")
    else:
        print(body)
    if failed:
        print("pr-gate: high-fragility change requires review", file=sys.stderr)
        return 1
    print("pr-gate: no high-fragility changed asset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
