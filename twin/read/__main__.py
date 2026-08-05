"""Read the estate and cache it: ``python -m twin.read``.

Prints what was read and where it landed. The last line is the summary line every later
stage echoes, so a nightly log shows at a glance whether the platform changed.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import time
from pathlib import Path
from typing import Iterable

from twin.read import gms_url, read_estate
from twin.read.cache import CACHE_DIR, load_latest, previous_fingerprint, store
from twin.read.mcp_client import DataHubMCPError
from twin.read.model import KIND_DATASET, EstateGraph

_LAYER_ORDER = ("raw_pg", "raw_events", "staging", "intermediate", "marts", "ml", "bi", "other")


def _print_report(graph: EstateGraph, elapsed: float, previous: str | None, cached: Path) -> None:
    by_layer = collections.Counter(a.layer for a in graph.assets)
    by_kind = collections.Counter(a.kind for a in graph.assets)
    fan_out = collections.Counter(e.source for e in graph.edges)

    print()
    print("  ESTATE GRAPH")
    print("  " + "-" * 68)
    print(f"  {'Read from':<14} {graph.source} over MCP in {elapsed:.1f}s")
    if previous is None:
        state = "first read"
    elif previous == graph.fingerprint:
        state = f"unchanged since {previous}"
    else:
        state = f"changed from {previous}"
    print(f"  {'Fingerprint':<14} {graph.fingerprint}  ({state})")
    print(f"  {'Cached at':<14} {cached}")

    print("\n  ASSETS BY LAYER")
    for layer in sorted(by_layer, key=lambda l: (_LAYER_ORDER.index(l) if l in _LAYER_ORDER else 99, l)):
        print(f"    {layer:<16} {by_layer[layer]:>4}")

    print("\n  ASSETS BY KIND")
    for kind, count in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {kind:<16} {count:>4}")

    datasets = graph.of_kind(KIND_DATASET)
    unowned = [a for a in datasets if not a.is_owned]
    with_sla = [a for a in datasets if a.sla_hours is not None]
    unreplicated = [a for a in datasets if a.replicated is False]

    print("\n  DEPENDENCIES")
    print(f"    {'table edges':<16} {len(graph.edges):>4}")
    print(f"    {'column edges':<16} {len(graph.column_edges):>4}")
    print(f"    {'columns read':<16} {sum(len(a.columns) for a in datasets):>4}")

    print("\n  OPERATIONAL METADATA")
    print(f"    {'with an SLA':<16} {len(with_sla):>4}")
    print(f"    {'unreplicated':<16} {len(unreplicated):>4}")
    print(f"    {'unowned':<16} {len(unowned):>4}")

    print("\n  WIDEST DIRECT FAN-OUT")
    for key, count in sorted(fan_out.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
        reach = len(graph.reachable_downstream(key))
        print(f"    {key:<44} {count:>2} direct, {reach:>2} total")
    print()


def _append_history(graph: EstateGraph, path: Path) -> None:
    """Append one line describing this read to the nightly history.

    The fragility trend is only real if the runs actually happened, so history accumulates
    one measured line per run and is never generated retrospectively. Later stages widen
    the record — scores join it when Stage 3 lands — but the shape stays append-only, and a
    line is written when a read succeeds and not otherwise.
    """
    datasets = graph.of_kind(KIND_DATASET)
    record = {
        "read_at": graph.read_at,
        "fingerprint": graph.fingerprint,
        "source": graph.source,
        "assets": len(graph.assets),
        "datasets": len(datasets),
        "edges": len(graph.edges),
        "column_edges": len(graph.column_edges),
        "unowned_datasets": sum(1 for a in datasets if not a.is_owned),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as history:
        history.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read the estate from DataHub over MCP.")
    parser.add_argument("--gms", default=gms_url(), help="DataHub GMS URL")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR, help="where graphs are cached")
    parser.add_argument("--concurrency", type=int, default=8, help="concurrent MCP calls")
    parser.add_argument("--debug", action="store_true", help="show the MCP server's own logging")
    parser.add_argument(
        "--cached",
        action="store_true",
        help="print the last cached graph without reading DataHub",
    )
    parser.add_argument(
        "--append-history",
        type=Path,
        metavar="PATH",
        help="append one JSON line describing this read, for the nightly trend",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cached:
        graph = load_latest(args.cache_dir)
        if graph is None:
            print("no cached graph; run without --cached first", file=sys.stderr)
            return 2
        print(f"\n  {graph.summary_line()}\n  read at {graph.read_at} from {graph.source}\n")
        return 0

    previous = previous_fingerprint(args.cache_dir)
    started = time.monotonic()
    try:
        graph = asyncio.run(read_estate(args.gms, concurrency=args.concurrency, debug=args.debug))
    except DataHubMCPError as exc:
        print(f"reading the estate failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not swallow
        print(f"cannot reach DataHub at {args.gms}: {exc}", file=sys.stderr)
        print("is the stack up? try `make up`.", file=sys.stderr)
        return 2
    elapsed = time.monotonic() - started

    if not graph.assets:
        print("DataHub returned no entities; has `make estate` run?", file=sys.stderr)
        return 1

    entry = store(graph, args.cache_dir)
    if args.append_history:
        _append_history(graph, args.append_history)
    _print_report(graph, elapsed, previous, entry.path)
    print(f"  {graph.summary_line()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
