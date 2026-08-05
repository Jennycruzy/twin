"""The DataHub MCP client — Twin's only route into the catalog.

Twin reads the estate through DataHub's official MCP server rather than through the Python
SDK or raw GraphQL. That is a deliberate constraint, not a convenience: the point of the
project is that an agent can inherit fragility as a dimension of the catalog, and an agent
reaches DataHub over MCP. Reading the same way the consumer reads keeps Twin honest about
what is actually reachable through that interface — including where it is thin, which the
README records rather than papers over.

The server is spawned as a stdio subprocess and speaks the same protocol it would speak to
any other client. Six tools are exposed against open-source DataHub: ``search``,
``get_entities``, ``get_lineage``, ``get_lineage_paths_between``, ``list_schema_fields`` and
``get_dataset_queries``. This module wraps the four Stage 1 needs and does nothing clever
with the rest.

Calls are issued concurrently under a bounded semaphore. Materialising the estate at column
grain is several hundred round trips, and doing them one at a time turns a nightly read into
minutes of sequential latency. Ordering is never relied on — every result is sorted by the
caller — so concurrency cannot leak into Twin's output.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from typing import Any, AsyncIterator, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# The server caps a single search page at 50 results.
_MAX_PAGE = 50

# get_entities is a batch call; the estate's entities are large enough that very wide
# batches produce unwieldy single responses without being meaningfully faster.
_ENTITY_BATCH = 20

# Deep lineage is not needed here. Twin builds the transitive picture itself from one-hop
# edges, so that the graph it reasons about is one it assembled and can explain.
_ONE_HOP = 1


class DataHubMCPError(RuntimeError):
    """A tool call failed, or returned something that was not the JSON it promised."""


class DataHubMCP:
    """A live MCP session against DataHub."""

    def __init__(self, session: ClientSession, concurrency: int) -> None:
        self._session = session
        self._gate = asyncio.Semaphore(concurrency)
        self.calls = 0

    # ---------------------------------------------------------------- lifecycle

    @classmethod
    @contextlib.asynccontextmanager
    async def connect(
        cls,
        gms_url: str,
        token: str | None = None,
        concurrency: int = 8,
        debug: bool = False,
    ) -> AsyncIterator["DataHubMCP"]:
        """Start the MCP server and hand back a connected client.

        The server's own logging goes to stderr and is verbose at INFO. It is discarded
        unless ``debug`` is set, so that Twin's output is Twin's output — a run that prints
        a hundred lines of someone else's DEBUG noise is a run nobody reads.
        """
        env = dict(os.environ)
        env["DATAHUB_GMS_URL"] = gms_url
        if token:
            env["DATAHUB_GMS_TOKEN"] = token
        # Twin must run with no outbound network access beyond the local stack.
        env["DATAHUB_TELEMETRY_ENABLED"] = "false"

        params = StdioServerParameters(
            command="mcp-server-datahub", args=["--transport", "stdio"], env=env
        )
        with open(os.devnull, "w") as devnull:
            errlog = sys.stderr if debug else devnull
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield cls(session, concurrency)

    # ---------------------------------------------------------------- raw call

    async def _call(self, tool: str, args: dict[str, Any]) -> Any:
        async with self._gate:
            result = await self._session.call_tool(tool, args)
        self.calls += 1

        text = "\n".join(c.text for c in result.content if hasattr(c, "text")).strip()
        if result.isError:
            raise DataHubMCPError(f"{tool}({args}) failed: {text[:400]}")
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataHubMCPError(f"{tool} returned non-JSON: {text[:200]}") from exc

    # ---------------------------------------------------------------- tools

    async def search(self, filter_expr: str, query: str = "*") -> list[dict[str, Any]]:
        """Every entity matching a filter, following pagination to the end.

        The total is re-read on each page rather than trusted from the first: the estate is
        static while Twin reads it, but a truncated read that silently looks complete is the
        kind of bug that produces a confident, wrong blast radius.
        """
        entities: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._call(
                "search",
                {
                    "query": query,
                    "filter": filter_expr,
                    "num_results": _MAX_PAGE,
                    "offset": offset,
                },
            )
            results = page.get("searchResults") or []
            entities.extend(r["entity"] for r in results if "entity" in r)
            offset += len(results)
            if not results or offset >= int(page.get("total", 0)):
                return entities

    async def get_entities(self, urns: Sequence[str]) -> list[dict[str, Any]]:
        """Full metadata for a list of URNs, in batches."""
        batches = [urns[i : i + _ENTITY_BATCH] for i in range(0, len(urns), _ENTITY_BATCH)]
        results = await asyncio.gather(*(self._call("get_entities", {"urns": list(b)}) for b in batches))
        return [entity for batch in results for entity in (batch or [])]

    async def upstreams(self, urn: str, column: str | None = None) -> list[dict[str, Any]]:
        """One hop of upstream lineage, optionally for a single column.

        Table-grain edges are read in this direction only. Both directions describe the same
        edges, and reading one of them means an edge is discovered exactly once rather than
        once from each end, where a disagreement between the two would have to be resolved.
        """
        return await self._lineage(urn, upstream=True, column=column)

    async def downstreams(self, urn: str, column: str | None = None) -> list[dict[str, Any]]:
        """One hop of downstream lineage, optionally for a single column.

        Column grain is read in this direction because it is the direction the question is
        asked in: this column is about to break, who reads it?
        """
        return await self._lineage(urn, upstream=False, column=column)

    async def _lineage(
        self, urn: str, upstream: bool, column: str | None
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "urn": urn,
            "upstream": upstream,
            "max_hops": _ONE_HOP,
            "max_results": _MAX_PAGE,
            "column": column,
        }
        block_name = "upstreams" if upstream else "downstreams"
        entities: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._call("get_lineage", {**args, "offset": offset})
            block = (page or {}).get(block_name) or {}
            results = block.get("searchResults") or []
            entities.extend(r["entity"] for r in results if "entity" in r)
            offset += len(results)
            if not results or offset >= int(block.get("total", 0)):
                return entities
