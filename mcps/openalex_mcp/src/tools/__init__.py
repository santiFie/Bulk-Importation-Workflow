"""
OpenAlex MCP Server — Tools Package
Registers all tool modules onto the FastMCP instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from openalex_client import OpenAlexClient


def register_all(mcp: "FastMCP", client: "OpenAlexClient") -> None:
    """Register all OpenAlex tool modules on the given FastMCP instance."""
    from tools import institutions, authors, works

    institutions.register(mcp, client)
    authors.register(mcp, client)
    works.register(mcp, client)
