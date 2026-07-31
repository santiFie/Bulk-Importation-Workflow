"""
DSpace MCP Server — Tools Package

Registers all tool modules onto the FastMCP instance.
Each module exposes a register(mcp, client) function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient


def register_all(mcp: "FastMCP", client: "DSpaceClient") -> None:
    """Register all DSpace tools on the given FastMCP instance."""
    from tools import search, communities, collections, items, epersons, export, workflow, import_tools

    search.register(mcp, client)
    communities.register(mcp, client)
    collections.register(mcp, client)
    items.register(mcp, client)
    epersons.register(mcp, client)
    export.register(mcp, client)
    workflow.register(mcp, client)
    import_tools.register(mcp, client)
