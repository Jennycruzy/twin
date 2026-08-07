"""On-disk cache for materialised estate graphs, keyed by fingerprint.

Reading the estate at column grain is several hundred MCP round trips. Later commands need
the same graph repeatedly — a scenario run, a knockout sweep over every asset, a CI gate —
and re-reading it each time would make those slow for no benefit, because the graph does
not change while they run.

Cached graphs are named by fingerprint rather than by timestamp, which gives the property
that matters: an unchanged platform read twice writes the same file, so the cache directory
accumulates one entry per distinct *state* of the estate rather than one per run. That is
also what lets a nightly run say whether the platform actually changed since yesterday.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from twin.read.model import EstateGraph

CACHE_DIR = Path(".twin")
_POINTER = "latest.json"


@dataclass(frozen=True)
class CacheEntry:
    """Where a graph landed on disk, and whether it was new."""

    path: Path
    fingerprint: str
    is_new_state: bool


def store(graph: EstateGraph, cache_dir: Path = CACHE_DIR) -> CacheEntry:
    """Write a graph to the cache and point ``latest`` at it."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"graph-{graph.fingerprint}.json"
    is_new_state = not path.exists()
    path.write_text(graph.to_json())
    (cache_dir / _POINTER).write_text(
        json.dumps(
            {
                "fingerprint": graph.fingerprint,
                "path": path.name,
                "read_at": graph.read_at,
                "source": graph.source,
            },
            indent=2,
        )
        + "\n"
    )
    return CacheEntry(path=path, fingerprint=graph.fingerprint, is_new_state=is_new_state)


def load(fingerprint: str, cache_dir: Path = CACHE_DIR) -> EstateGraph:
    """Load one specific state of the estate."""
    return EstateGraph.from_dict(json.loads((cache_dir / f"graph-{fingerprint}.json").read_text()))


def load_latest(cache_dir: Path = CACHE_DIR) -> EstateGraph | None:
    """The most recently read graph, or ``None`` if the estate has never been read."""
    pointer = cache_dir / _POINTER
    if not pointer.exists():
        return None
    target = cache_dir / json.loads(pointer.read_text())["path"]
    if not target.exists():
        return None
    return EstateGraph.from_dict(json.loads(target.read_text()))


def previous_fingerprint(cache_dir: Path = CACHE_DIR) -> str | None:
    """The fingerprint recorded before the current read, for change detection."""
    pointer = cache_dir / _POINTER
    if not pointer.exists():
        return None
    return json.loads(pointer.read_text()).get("fingerprint")
