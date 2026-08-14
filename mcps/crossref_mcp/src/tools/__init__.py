"""
Crossref MCP Server — Paquete de Tools
Registra todos los módulos de herramientas en la instancia de FastMCP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from crossref_client import CrossrefClient


def register_all(mcp: "FastMCP", client: "CrossrefClient") -> None:
    """Registra todos los módulos de tools de Crossref en la instancia FastMCP."""
    from tools import works, journals, funders, members, misc

    works.register(mcp, client)
    journals.register(mcp, client)
    funders.register(mcp, client)
    members.register(mcp, client)
    misc.register(mcp, client)
