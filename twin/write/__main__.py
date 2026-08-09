"""Write Twin's dimensions into DataHub, prove them, or remove them.

    python -m twin.write              define the properties and write every score
    python -m twin.write --prove      read the values back over MCP and print them
    python -m twin.write --unwrite    remove everything Twin wrote

``--prove`` is the one that matters. Writing to a catalog and then reading back through the
same SDK proves only that the SDK is self-consistent. Reading back over MCP proves the score
is visible through the interface another agent would actually use to find it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Iterable

from twin import provenance
from twin.context import confidence as context_confidence, evidence_path, verified_assets
from twin.read import gms_url, read_estate
from twin.read.cache import load_latest, store
from twin.read.mcp_client import DataHubMCP
from twin.read.model import KIND_DATASET, EstateGraph
from twin.score.fragility import CONFIG, Score, Weights, score_estate
from twin.score.cost import CostModel
from twin.score.knockout import sweep
from twin.score.usage import read_usage
from twin.write.catalog import Catalog, WriteBackError
from twin.write.incidents import raised_on, resolve_all
from twin.write.properties import DEFINITIONS, PREFIX, values_for
from twin.target import TwinTarget, load_target

_RULE = "  " + "-" * 74

# get_entities is batched: one call naming every dataset is a large response.
_BATCH = 20


def _load_graph(target: TwinTarget, refresh: bool) -> EstateGraph:
    if not refresh:
        cached = load_latest(target.cache_dir)
        if cached is not None:
            return cached
    graph = asyncio.run(read_estate(scope=target.catalog))
    store(graph, target.cache_dir)
    return graph


def _scored(
    graph: EstateGraph, config: Path, target: TwinTarget
) -> tuple[Score, ...]:
    weights = Weights.load(config)
    return score_estate(
        graph, sweep(graph), read_usage(scope=target.catalog), weights, CostModel.load()
    )


def _dataset_urns(graph: EstateGraph, key: str) -> tuple[str, ...]:
    """Every dataset URN this logical asset folds together.

    An asset is usually two entities — the warehouse table and its dbt sibling — and a person
    in the UI may open either. Writing the score to both means the answer is where they
    looked, rather than where Twin happened to prefer.
    """
    if not graph.has(key):
        return ()
    asset = graph.asset(key)
    if asset.kind != KIND_DATASET:
        return ()
    return tuple(urn for urn in asset.urns if urn.startswith("urn:li:dataset:"))


def _provenance_line(graph: EstateGraph) -> str:
    stamp = provenance.stamp()
    return (
        f"graph {graph.fingerprint}; commit {stamp['commit'] or 'unknown'}"
        f"{' (dirty)' if stamp['dirty'] else ''}; "
        f"weights {provenance.digest_of(CONFIG) or 'unknown'}"
    )


def _write(graph: EstateGraph, config: Path, target: TwinTarget) -> int:
    usage = read_usage(scope=target.catalog)
    cost_model = CostModel.load()
    scores = score_estate(graph, sweep(graph), usage, Weights.load(config), cost_model)
    catalog = Catalog.connect()

    defined = catalog.bootstrap()
    print(f"\n  defined {len(defined)} structured properties")

    line = _provenance_line(graph)
    # The campaign ledger is what makes verification evidence rather than assertion. Reading
    # it here is what lets a published property distinguish an asset Twin has actually broken
    # from one it has only reasoned about; without it every asset reports verification=0.00
    # no matter how many experiments have run.
    verified = verified_assets(evidence_path(target.cache_dir), graph.fingerprint)
    written = 0
    skipped = []
    for rank, score in enumerate(scores, start=1):
        urns = _dataset_urns(graph, score.key)
        if not urns:
            skipped.append(score.key)
            continue
        values = values_for(
            score, rank, graph.read_at, line,
            context=context_confidence(graph, score.key, usage, verified),
        )
        for urn in urns:
            catalog.write_values(urn, values)
        written += 1

    print(f"  wrote fragility to {written} assets ({len(DEFINITIONS)} properties each)")
    if skipped:
        # Named rather than counted. A silently skipped asset is the difference between "the
        # estate is scored" and "most of it is", and only one of those is true.
        print(f"  skipped {len(skipped)} with no dataset URN: {', '.join(sorted(skipped)[:5])}")
    print(f"  provenance: {line}")
    print(f"  blast-radius cost is illustrative, {cost_model.assumptions_line()}")
    print("\n  prove it with: make prove-writeback")
    print("  remove it with: make unwrite\n")
    return 0


def _prove(graph: EstateGraph, limit: int) -> int:
    """Read Twin's properties back out of DataHub over MCP and print what came back.

    Every scored asset is read, not a sample, and the table is ordered by the rank that came
    back from the catalog rather than by a ranking recomputed here. That ordering is the
    point: if the number in DataHub disagrees with the number Twin computed, this is where it
    shows, and a table sorted by a locally recomputed rank would hide exactly that.
    """
    by_urn = {}
    for asset in graph.of_kind(KIND_DATASET):
        for urn in _dataset_urns(graph, asset.key)[:1]:
            by_urn[urn] = asset.key
    if not by_urn:
        print("no dataset URNs in the cached graph; run `make read` first", file=sys.stderr)
        return 2

    urns = list(by_urn)

    async def read_back() -> list:
        entities: list = []
        async with DataHubMCP.connect(gms_url()) as mcp:
            # Batched because a single call naming every dataset is a large response and the
            # server is under no obligation to return it whole.
            for start in range(0, len(urns), _BATCH):
                page = await mcp._call("get_entities", {"urns": urns[start : start + _BATCH]})
                entities.extend(page if isinstance(page, list) else [page])
        return entities

    rows = []
    for entity in asyncio.run(read_back()):
        values = _twin_values(entity)
        if not values:
            continue
        rows.append((by_urn.get(str(entity.get("urn", "")), "?"), values))
    rows.sort(key=lambda row: _number(row[1].get(f"{PREFIX}fragility_rank")) or 1e9)

    print()
    print("  FRAGILITY, READ BACK OVER MCP")
    print(_RULE)
    print(f"  {'RANK':>5}  {'ASSET':<40}{'SCORE':>8}{'CTX':>6}{'BLAST':>7}{'COST':>10}{'BUS':>5}{'SPOF':>6}")
    print(_RULE)
    for key, values in rows[:limit]:
        print(
            f"  {_as_int(values.get(f'{PREFIX}fragility_rank')):>5}  {key[:40]:<40}"
            f"{_number(values.get(f'{PREFIX}fragility_score')) or 0:>8.3f}"
            f"{_number(values.get(f'{PREFIX}context_confidence')) or 0:>6.2f}"
            f"{_as_int(values.get(f'{PREFIX}blast_radius')):>7}"
            f"${_number(values.get(f'{PREFIX}blast_radius_cost')) or 0:>9,.2f}"
            f"{_as_int(values.get(f'{PREFIX}bus_factor')):>5}"
            f"{str(values.get(f'{PREFIX}is_spof', '—')):>6}"
        )
    print(_RULE)
    print(f"  {len(rows)} of {len(urns)} assets carry Twin's properties, read over MCP")
    print(f"  blast-radius cost is illustrative, {CostModel.load().assumptions_line()}")
    if rows:
        print(f"  provenance in the catalog: {rows[0][1].get(f'{PREFIX}scoring_provenance', '—')}")
    print()
    if not rows:
        print("  nothing found — has `make writeback` run?\n", file=sys.stderr)
        return 1
    return 0


def _twin_incidents(catalog: Catalog, graph: EstateGraph):
    """Every incident Twin raised, read back from the assets it raised them against.

    One URN per asset is enough: an incident is attached to every sibling the asset folds, so
    either resolves it. Verified against the live catalog rather than assumed.
    """
    urns = tuple(u for a in graph.of_kind(KIND_DATASET) for u in _dataset_urns(graph, a.key)[:1])
    return raised_on(catalog, urns)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _as_int(value: object) -> str:
    """DataHub stores every number as a float; a rank of 18.0 should read as 18."""
    number = _number(value)
    return "—" if number is None else str(int(number))


def _twin_values(entity: object) -> dict[str, object]:
    """Pull Twin's structured-property values out of an MCP ``get_entities`` payload."""
    if not isinstance(entity, dict):
        return {}
    block = entity.get("structuredProperties") or {}
    found = {}
    for assignment in block.get("properties", []) if isinstance(block, dict) else []:
        definition = (assignment.get("structuredProperty") or {}).get("definition") or {}
        name = definition.get("qualifiedName", "")
        if not name.startswith(PREFIX):
            continue
        values = assignment.get("values") or []
        if not values:
            continue
        first = values[0]
        found[name] = first.get("numberValue", first.get("stringValue")) if isinstance(first, dict) else first
    return found


def _unwrite(graph: EstateGraph, purge: bool) -> int:
    urns = [urn for a in graph.of_kind(KIND_DATASET) for urn in _dataset_urns(graph, a.key)]
    catalog = Catalog.connect()
    cleared, deleted = catalog.unwrite(tuple(urns), purge=purge)
    sweep = _twin_incidents(catalog, graph)
    resolved = resolve_all(catalog, sweep.found)
    print(f"\n  cleared Twin's values from {cleared} assets")
    if resolved:
        print(f"  resolved {resolved} incidents Twin raised (resolved, not deleted)")
    if purge:
        print(f"  deleted {deleted} property definitions")
        print("  note: DataHub will not let these names be defined again on this stack.")
        print("        `make writeback` will fail until the search index is rebuilt.")
    else:
        print(f"  left {len(DEFINITIONS)} property definitions in place, holding no values")
        print("  they appear on no asset; deleting them would burn the names for good.")
        print("  use --purge to delete them anyway, and read twin/write/catalog.py first.")
    print()

    if not sweep.is_complete:
        # An incomplete sweep is a failed unwrite, not a caveat on a successful one. The
        # values were cleared, but Twin cannot say the incidents were, and a target that
        # exits 0 here would let a nightly or a CI step move on as though it had.
        print("  incident sweep did not complete:", file=sys.stderr)
        for urn, error in sweep.unreachable[:5]:
            print(f"    unreadable  {urn}\n                {error}", file=sys.stderr)
        for urn in sweep.truncated[:5]:
            print(f"    truncated   {urn} lists more incidents than were returned", file=sys.stderr)
        print(
            f"  {len(sweep.unreachable)} unreadable, {len(sweep.truncated)} truncated. "
            "Some of Twin's incidents may still be active — re-run to retry.\n",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write Twin's fragility scores into DataHub.")
    parser.add_argument("--target", help="estate target name from targets/<name>.yml")
    parser.add_argument("--refresh", action="store_true", help="re-read the estate first")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--prove", action="store_true", help="read the values back over MCP")
    parser.add_argument("--unwrite", action="store_true", help="remove every value Twin wrote")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="with --unwrite, also delete the definitions (burns the names, see catalog.py)",
    )
    parser.add_argument("--limit", type=int, default=15, help="rows for --prove")
    args = parser.parse_args(list(argv) if argv is not None else None)
    target = load_target(args.target)

    graph = _load_graph(target, args.refresh)
    try:
        if args.prove:
            return _prove(graph, args.limit)
        if args.unwrite:
            return _unwrite(graph, args.purge)
        return _write(graph, args.config, target)
    except WriteBackError as exc:
        print(f"write-back failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"cannot run write-back: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
