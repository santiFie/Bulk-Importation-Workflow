"""
Deduplicator MCP Server — Entrypoint

Starts a FastMCP server with SSE transport on 0.0.0.0:5100.

Startup sequence:
  1. Read the Django service URL from environment (via config.py)
  2. Instantiate the DeduplicatorClient
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
from config import DEDUPLICATOR_BASE_URL
from deduplicator_client import DeduplicatorClient
from tools import register_all

client = DeduplicatorClient(DEDUPLICATOR_BASE_URL)

mcp = FastMCP(
    name="Deduplicator MCP",
    instructions=(
        "This MCP server provides tools to interact with the Duplicate Detector Django REST API. "
        "It offers two capabilities:\n\n"
        "  • detect_duplicates — Receives two CSV document lists and returns a duplicate report "
        "using Levenshtein similarity rules on title, authors, and publication date. "
        "The result is saved as a CSV file in /app/data.\n\n"
        "  • check_authority — Receives a document list and an author reference list and returns "
        "a coincidence report with quality scores (PERFECT, GOOD, REGULAR, MMM). "
        "The author list can be in Scopus format or simple Apellido/Nombre format. "
        "The result is saved as a CSV file in /app/data.\n\n"
        "All CSV input files must be accessible at their absolute paths inside the container. "
        "The Django service is expected to be running at the URL configured via DEDUPLICATOR_BASE_URL."
    ),
)

register_all(mcp, client)

if __name__ == "__main__":
    logger.info("Starting Deduplicator MCP Server on 0.0.0.0:5100 (SSE transport)")
    logger.info("Connecting to Django service at %s", DEDUPLICATOR_BASE_URL)
    mcp.run(transport="sse", host="0.0.0.0", port=5100)
