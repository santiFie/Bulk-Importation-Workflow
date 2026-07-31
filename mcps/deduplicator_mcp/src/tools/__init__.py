"""
Deduplicator MCP — Tools Package

Registers all tool modules onto the FastMCP instance.
Each module exposes a register(mcp, client) function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from deduplicator_client import DeduplicatorClient


def register_all(mcp: "FastMCP", client: "DeduplicatorClient") -> None:
    """Register all Deduplicator tools on the given FastMCP instance."""
    from tools import deduplicator, authority

    deduplicator.register(mcp, client)
    authority.register(mcp, client)
