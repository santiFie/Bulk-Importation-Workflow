"""
DSpace MCP Server — Entrypoint

Starts a FastMCP server with SSE transport on 0.0.0.0:5000.

Startup sequence:
  1. Read credentials from environment (via config.py)
  2. Authenticate against DSpace and obtain a JWT
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
from config import BASE_URL, EMAIL, PASSWORD
from dspace_client import DSpaceClient
from tools import register_all

# Always create the DSpace client with the same credentials
client = DSpaceClient(BASE_URL, EMAIL, PASSWORD)

mcp = FastMCP(
    name="DSpace MCP",
    instructions=(
        "This MCP server provides tools to interact with a DSpace 7+ institutional repository. "
        "You can search, list, create and update communities, collections, and items. "
        "You can export collection metadata as CSV and import items in bulk using the "
        "DSpace Simple Archive Format (SAF). Import operations accept a local SAF directory, "
        "zip it automatically, and upload it to the DSpace Scripts API. "
        "All operations require admin-level access, which is handled automatically. "
        "UUIDs are used to identify all DSpace objects."
    ),
)

# Register all tool modules
register_all(mcp, client)

if __name__ == "__main__":
    logger.info("Connecting to DSpace at %s", BASE_URL)
    try:
        client.login()
    except Exception as exc:
        logger.critical("Failed to authenticate against DSpace: %s", exc)
        sys.exit(1)

    logger.info("Starting DSpace MCP Server on 0.0.0.0:5000 (SSE transport)")
    mcp.run(transport="sse", host="0.0.0.0", port=5000)
