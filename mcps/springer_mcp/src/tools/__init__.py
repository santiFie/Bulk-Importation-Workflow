"""
Springer MCP Server — Tools Package
Registers all tool modules onto the FastMCP instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from springer_client import SpringerClient


def register_all(mcp: "FastMCP", client: "SpringerClient") -> None:
    """Register all Springer tool modules on the given FastMCP instance."""
    from tools import search, articles, export

    search.register(mcp, client)
    articles.register(mcp, client)
    export.register(mcp, client)
