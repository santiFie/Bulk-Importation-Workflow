"""
OpenAlex MCP Server — Entrypoint

Starts a FastMCP server via stdio transport.

Startup sequence:
  1. Read API key / email from environment (via config.py)
  2. Instantiate the OpenAlexClient
  3. Register all tools on the MCP instance
  4. Run via stdio (compatible with LangGraph / langchain-mcp-adapters)
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,  # stdio transport uses stdout; keep logs on stderr
)
logger = logging.getLogger(__name__)

from fastmcp import FastMCP
from openalex_client import OpenAlexClient
from tools import register_all

client = OpenAlexClient()

mcp = FastMCP(
    name="OpenAlex MCP",
    instructions=(
        "This MCP server provides tools to query the OpenAlex academic knowledge graph. "
        "It covers three entity types:\n"
        "  • Institutions — universities, research centres, hospitals, etc.\n"
        "  • Authors      — researcher profiles with impact metrics and affiliations.\n"
        "  • Works        — scientific articles, books, datasets, pre-prints, etc.\n\n"
        "All search/list tools accept an optional `select` parameter (comma-separated field names) "
        "to control which fields are returned, reducing context-window usage. "
        "Use `filter` for precise server-side filtering (e.g. by country, institution, or year). "
        "IDs must be resolved to OpenAlex entity IDs before using them as filter values — "
        "use the search tools first to obtain the numeric ID."
    ),
)

register_all(mcp, client)

if __name__ == "__main__":
    logger.info("Starting OpenAlex MCP Server (stdio transport)")
    mcp.run(transport="stdio")
