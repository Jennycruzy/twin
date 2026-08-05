"""Stage 5: the fragility dimension Twin adds to DataHub.

Written through the SDK because the MCP server exposes no write tool, and read back over MCP
because that is the interface the claim is about. See :mod:`twin.write.properties`.
"""

from twin.write.catalog import Catalog, WriteBackError
from twin.write.properties import DEFINITIONS, PREFIX, values_for

__all__ = ["Catalog", "WriteBackError", "DEFINITIONS", "PREFIX", "values_for"]
