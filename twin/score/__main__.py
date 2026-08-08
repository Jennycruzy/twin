"""Score the estate: ``make score``.

Prints a ranked table with every component visible beside the total, because a fragility
number nobody can take apart is a number nobody should act on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Iterable

from twin import provenance
from twin.context import all_confidence, evidence_path, verified_assets
from twin.read import read_estate
from twin.read.cache import load_latest, store
from twin.read.model import EstateGraph
from twin.score.fragility import COMPONENTS, CONFIG, Coverage, Score, Weights, score_estate
from twin.score.cost import CostModel
from twin.score.knockout import sweep
from twin.score.usage import read_usage
from twin.target import TwinTarget, load_target

_RULE = "  " + "-" * 92


def _load_graph(target: TwinTarget, refresh: bool) -> EstateGraph:
    if not refresh:
        cached = load_latest(target.cache_dir)
        if cached is not None:
            return cached
    graph = asyncio.run(read_estate(scope=target.catalog))
    store(graph, target.cache_dir)
    return graph


def _print_table(scores: tuple[Score, ...], limit: int) -> None:
    print()
    print("  FRAGILITY")
    print(_RULE)
    header = "  ".join(f"{name[:5]:>5}" for name in COMPONENTS)
    print(f"  {'#':>2}  {'ASSET':<40} {'SCORE':>6}   {header}   BLAST     COST")
    print(_RULE)
    for rank, score in enumerate(scores[:limit], start=1):
        parts = "  ".join(f"{score.components[name]:>5.2f}" for name in COMPONENTS)
        shot = score.knockout
        print(
            f"  {rank:>2}  {score.key:<40} {score.score:>6.1f}   {parts}   "
            f"{len(shot.datasets_lost)}+{len(shot.consumers_lost)}  ${score.blast_radius_cost:>8,.2f}"
        )
    print(_RULE)


def _print_top(score: Score) -> None:
    """Explain the top finding in full, because a ranking is only as good as its reasons."""
    shot = score.knockout
    print()
    print(f"  TOP FINDING — {score.key}")
    print(_RULE)
    for name in COMPONENTS:
        print(f"    {name:<16} {score.components[name]:>5.2f}   (raw {score.raw[name]:,.0f})"
              if name in ("blast", "exposure")
              else f"    {name:<16} {score.components[name]:>5.2f}")
    print()
    print(f"    knocking it out takes {len(shot.datasets_lost)} dataset(s) and "
          f"{len(shot.consumers_lost)} consumer(s) with it")
    print(f"    {len(set(shot.owners_paged))} owner(s) would be paged, "
          f"{len(shot.unowned_in_radius)} asset(s) in the radius have no owner")
    print(f"    blast radius cost ${score.blast_radius_cost:,.2f}")
    if shot.first_consumer_at is not None:
        hours = shot.first_consumer_at.total_seconds() / 3600
        print(f"    first consumer affected {hours:.1f}h after the fault")


def _append_history(
    graph: EstateGraph,
    scores: tuple[Score, ...],
    path: Path,
    cost_model: CostModel,
    target: str | None = None,
) -> None:
    """Append one line of measured fragility to the nightly record.

    The trend is the claim that cannot be backfilled: a fragility score climbing over three
    weeks is only evidence if the runs happened on those days. Each line carries the graph
    fingerprint it was computed from, so a change in the ranking can be attributed to the
    platform changing rather than to the model changing.

    It also carries the commit and a digest of the weights file, which is what makes that
    attribution checkable rather than asserted: the estate moving and the model moving are
    the two explanations for a ranking that shifts, and a trend that cannot separate them is
    not measuring anything. See :mod:`twin.provenance`.
    """
    record = {
        **provenance.stamp(),
        "target": target,
        "scoring_config": provenance.digest_of(CONFIG),
        "cost_model_config": provenance.digest_of(cost_model.path),
        "scored_at": graph.read_at,
        "fingerprint": graph.fingerprint,
        "assets_scored": len(scores),
        "mean_score": round(sum(s.score for s in scores) / len(scores), 3) if scores else 0.0,
        "top": [
            {
                "key": s.key,
                "score": round(s.score, 3),
                "blast_radius_cost": round(s.blast_radius_cost, 2),
            }
            for s in scores[:5]
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as history:
        history.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank the estate by fragility.")
    parser.add_argument("--target", help="estate target name from targets/<name>.yml")
    parser.add_argument("--refresh", action="store_true", help="re-read the estate first")
    parser.add_argument("--limit", type=int, default=15, help="rows to print")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument(
        "--append-history",
        type=Path,
        metavar="PATH",
        help="append one JSON line of scores, for the nightly trend",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    target = load_target(args.target)

    graph = _load_graph(target, args.refresh)
    try:
        weights = Weights.load(args.config)
        cost_model = CostModel.load()
    except (OSError, ValueError) as exc:
        print(f"cannot load scoring weights: {exc}", file=sys.stderr)
        return 2

    usage = read_usage(scope=target.catalog)
    knockouts = sweep(graph)
    scores = score_estate(graph, knockouts, usage, weights, cost_model)

    print()
    coverage = Coverage.measure(graph, usage)
    print(f"  estate {graph.fingerprint} — {len(scores)} assets swept")
    print(
        f"  metadata coverage: replication {coverage.replication:.0%}, "
        f"usage {coverage.usage:.0%}, ownership {coverage.ownership:.0%}, "
        f"tiers {coverage.tiers:.0%}"
    )
    contexts = all_confidence(
        graph, usage, verified_assets(evidence_path(target.cache_dir), graph.fingerprint)
    )
    mean_context = sum(item.score for item in contexts) / len(contexts) if contexts else 0.0
    low_context = sum(item.state == "low" for item in contexts)
    print(f"  context confidence: mean {mean_context:.0%}, low-context assets {low_context}")
    if coverage.replication < 0.5:
        print("  note: recovery rests on replication metadata that most assets lack;")
        print("        the ranking is falling back toward fan-out")
    if args.append_history:
        _append_history(graph, scores, args.append_history, cost_model, target.name)
    _print_table(scores, args.limit)
    _print_top(scores[0])
    print()
    print("  Scores are positions within this estate, not absolute values; two estates'")
    print("  numbers are not comparable. Weights are in config/scoring.yml and every")
    print("  component above is measured — see docs/SCORING.md.")
    print(f"  Blast-radius cost is illustrative, {cost_model.assumptions_line()}.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
