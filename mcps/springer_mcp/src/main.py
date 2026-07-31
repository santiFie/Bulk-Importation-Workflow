"""
Springer MCP Server — Entrypoint

Starts a FastMCP server via SSE transport on 0.0.0.0:5200.

Startup sequence:
  1. Read Springer API key from environment (via config.py)
  2. Instantiate the SpringerClient
  3. Register all tools on the MCP instance
  4. Serve SSE connections on /sse
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from fastmcp import FastMCP
from config import SPRINGER_API_KEY
from springer_client import SpringerClient
from tools import register_all

if not SPRINGER_API_KEY:
    logger.error(
        "SPRINGER_API_KEY environment variable is not set. "
        "Register a free key at https://dev.springernature.com/"
    )

client = SpringerClient()

mcp = FastMCP(
    name="Springer MCP",
    instructions=(
        "This MCP server provides tools to query the Springer Nature Open Access API "
        "and the full Springer Nature Metadata API.\n\n"
        "Available tools:\n"
        "  • search_open_access      — Search freely available (OA) articles using a Lucene query.\n"
        "  • search_metadata         — Search the full catalogue (OA + subscription content).\n"
        "  • get_article_by_doi      — Retrieve a single article by its DOI.\n"
        "  • export_search_to_csv    — Search and export results as a full-field CSV to /app/data.\n"
        "  • export_for_deduplication — Search and export results as a deduplication-ready CSV\n"
        "                              (source, title, id, author, date) compatible with the\n"
        "                              Deduplicator MCP's detect_duplicates tool.\n\n"
        "Query syntax (Lucene-style field prefixes):\n"
        "  title:, doi:, issn:, keyword:, pub:, subject:, type:, country:, year:,\n"
        "  onlinedate:[YYYY-MM-DD TO YYYY-MM-DD]\n"
        "  Combine with AND / OR operators.\n\n"
        "All CSV output files are written to /app/data inside the container "
        "(bind-mounted from the host at ./mcps/springer_mcp/data)."
    ),
)

register_all(mcp, client)

if __name__ == "__main__":
    logger.info("Starting Springer MCP Server on 0.0.0.0:5200 (SSE transport)")
    mcp.run(transport="sse", host="0.0.0.0", port=5200)
