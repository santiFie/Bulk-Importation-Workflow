"""
Springer MCP — Search Tools

Exposes two MCP tools for querying the Springer Nature API:
  • search_open_access  — Search the OA collection with a Lucene query.
  • search_metadata     — Search the full metadata catalogue (OA + subscription).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from springer_client import SpringerClient

logger = logging.getLogger(__name__)


def register(mcp: "FastMCP", client: "SpringerClient") -> None:

    @mcp.tool()
    def search_open_access(
        q: str,
        page_size: int = 25,
        start: int = 1,
    ) -> dict[str, Any]:
        """
        Search the Springer Nature Open Access collection using a Lucene-style query.

        This endpoint returns only freely available (open access) articles.
        Use ``search_metadata`` if you also need subscription-only content.

        Query syntax — field-scoped examples:
          title:"deep learning"
          doi:10.1007/s00253-020-10496-4
          issn:1432-0428
          keyword:bioinformatics
          pub:"Nature Medicine"
          subject:"Computer Science"
          type:"Journal Article"
          country:Germany
          onlinedate:[2022-01-01 TO 2023-12-31]
          year:2023

        Combine fields with AND / OR:
          title:"machine learning" AND subject:"Medicine" AND year:2022

        Args:
            q:         Lucene-style query string (required).
            page_size: Number of records per page (default 25, max 100).
            start:     1-based start record for pagination (default 1).

        Returns:
            A dict with:
              - total        : total matching records (int)
              - start        : current start offset
              - page_length  : page size used
              - records_displayed : records on this page
              - records      : list of article objects
        """
        try:
            data = client.search_open_access(q=q, page_size=page_size, start=start)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Springer API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        result_meta = data.get("result", [{}])[0]
        return {
            "total": int(result_meta.get("total", 0)),
            "start": int(result_meta.get("start", start)),
            "page_length": int(result_meta.get("pageLength", page_size)),
            "records_displayed": int(result_meta.get("recordsDisplayed", 0)),
            "records": data.get("records", []),
        }

    @mcp.tool()
    def search_metadata(
        q: str,
        page_size: int = 25,
        start: int = 1,
    ) -> dict[str, Any]:
        """
        Search the full Springer Nature Metadata catalogue (open access AND subscription content).

        Use this tool when you need broader coverage beyond freely available articles.
        The response structure is identical to ``search_open_access``.

        Query syntax — field-scoped examples:
          title:"cancer immunotherapy"
          doi:10.1038/s41586-021-03964-9
          keyword:CRISPR
          pub:"Scientific Reports"
          subject:"Life Sciences"
          type:"Book Chapter"
          onlinedate:[2020-01-01 TO 2021-12-31]
          year:2021

        Args:
            q:         Lucene-style query string (required).
            page_size: Number of records per page (default 25, max 100).
            start:     1-based start record for pagination (default 1).

        Returns:
            A dict with:
              - total        : total matching records (int)
              - start        : current start offset
              - page_length  : page size used
              - records_displayed : records on this page
              - records      : list of article objects
        """
        try:
            data = client.search_metadata(q=q, page_size=page_size, start=start)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Springer API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        result_meta = data.get("result", [{}])[0]
        return {
            "total": int(result_meta.get("total", 0)),
            "start": int(result_meta.get("start", start)),
            "page_length": int(result_meta.get("pageLength", page_size)),
            "records_displayed": int(result_meta.get("recordsDisplayed", 0)),
            "records": data.get("records", []),
        }
