"""
Springer MCP — Article Tools

Exposes one MCP tool:
  • get_article_by_doi — Retrieve a single OA article by its DOI.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from springer_client import SpringerClient

logger = logging.getLogger(__name__)


def register(mcp: "FastMCP", client: "SpringerClient") -> None:

    @mcp.tool()
    def get_article_by_doi(doi: str) -> dict[str, Any]:
        """
        Retrieve a single Springer Nature Open Access article by its DOI.

        Args:
            doi: The article DOI without the URL prefix, e.g.
                 '10.1007/s00253-020-10496-4' or '10.1038/s41586-021-03964-9'.

        Returns:
            The first matching article record dict, or an error dict.
            Common fields in the record:
              - identifier      : Springer internal identifier
              - title           : article title
              - creators        : list of {"creator": "Lastname Firstname"} dicts
              - publicationName : journal / book name
              - publisher       : publisher name
              - doi             : Digital Object Identifier
              - publicationDate : ISO date string (YYYY-MM-DD)
              - publicationType : e.g. "Journal", "Book"
              - openaccess      : "true" or "false"
              - abstract        : plain-text abstract
              - subject         : list of subject category strings
              - keyword         : list of keyword strings
              - url             : list of {"format": ..., "platform": ..., "value": ...} dicts
        """
        try:
            data = client.get_article_by_doi(doi=doi)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Springer API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        records = data.get("records", [])
        if not records:
            return {"error": f"No article found for DOI: {doi}"}

        return records[0]
