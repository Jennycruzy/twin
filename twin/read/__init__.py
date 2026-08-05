"""Stage 1 — Read.

The estate lives in DataHub. Twin reads it through DataHub's official MCP server and turns
it into an :class:`~twin.read.model.EstateGraph`, which is the only thing the rest of the
pipeline consumes. Every later stage takes a graph and never a catalog connection, so
simulation, scoring and verification can all be exercised against a cached graph with no
DataHub instance in sight.
"""

from __future__ import annotations

import asyncio
import os

from twin.read.cache import CacheEntry, load_latest, store
from twin.read.materialize import materialize
from twin.read.mcp_client import DataHubMCP, DataHubMCPError
from twin.read.model import Asset, Column, ColumnEdge, Edge, EstateGraph

DEFAULT_GMS_URL = "http://datahub-gms:8080"

__all__ = [
    "Asset",
    "Column",
    "ColumnEdge",
    "CacheEntry",
    "DataHubMCP",
    "DataHubMCPError",
    "Edge",
    "EstateGraph",
    "gms_url",
    "read_estate",
    "read_estate_sync",
]


def gms_url() -> str:
    return os.environ.get("DATAHUB_GMS_URL") or DEFAULT_GMS_URL


async def read_estate(
    url: str | None = None, concurrency: int = 8, debug: bool = False
) -> EstateGraph:
    """Read the estate from DataHub over MCP."""
    target = url or gms_url()
    async with DataHubMCP.connect(
        target, token=os.environ.get("DATAHUB_GMS_TOKEN") or None, concurrency=concurrency, debug=debug
    ) as client:
        return await materialize(client, source=target)


def read_estate_sync(url: str | None = None, concurrency: int = 8) -> EstateGraph:
    """Synchronous entry point for the stages that are not async."""
    return asyncio.run(read_estate(url, concurrency=concurrency))
