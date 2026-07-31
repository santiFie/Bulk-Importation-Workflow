"""
OpenAlex MCP — Authors Tools

Exposes two tools:
  • search_authors  — free-text / filter search across all authors.
  • get_author       — retrieve a single author by ID or ORCID.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from openalex_client import OpenAlexClient

logger = logging.getLogger(__name__)


def register(mcp: "FastMCP", client: "OpenAlexClient") -> None:

    @mcp.tool()
    def search_authors(
        search: str,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """
        Search OpenAlex authors by name or filter expression.

        Args:
            search: Free-text author name query (e.g. 'Carlos García' or 'Jane Doe').
            filter: OpenAlex filter string. Examples:
                    - 'last_known_institutions.id:I33213144'   (by institution)
                    - 'last_known_institutions.country_code:AR' (by country)
                    - 'works_count:>50'                        (by productivity)
                    - 'cited_by_count:>1000'                   (by impact)
                    Full list at: https://docs.openalex.org/api-reference/authors/filter-authors
            select: Comma-separated field names to include in each result.
                    Common fields: id, display_name, orcid, works_count, cited_by_count,
                    h_index, last_known_institutions, affiliations, topics,
                    summary_stats.
                    Omit to get all default fields.
            sort: Sort expression (e.g. 'cited_by_count:desc' or 'works_count:desc').
            page: 1-based page number (default 1).
            per_page: Number of results per page, max 200 (default 25).

        Returns:
            A dict with:
              - meta.count     : total matching authors
              - meta.page      : current page
              - meta.per_page  : page size
              - results        : list of author objects
        """
        try:
            data = client.search_authors(
                search=search,
                filter=filter,
                select=select,
                sort=sort,
                page=page,
                per_page=per_page,
            )
        except httpx.HTTPStatusError as exc:
            return {"error": f"OpenAlex API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        return {
            "meta": data.get("meta", {}),
            "results": data.get("results", []),
        }

    @mcp.tool()
    def get_author(
        author_id: str,
        select: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve a single author by their OpenAlex ID or ORCID.

        Args:
            author_id: OpenAlex author ID (e.g. 'A2208157607') or ORCID in the
                       format 'orcid:0000-0002-1825-0097'.
            select: Comma-separated list of fields to return.
                    Common fields: id, display_name, orcid, works_count, cited_by_count,
                    h_index, i10_index, last_known_institutions, affiliations, topics,
                    summary_stats, counts_by_year.
                    Omit to get all default fields.

        Returns:
            A single author object dict, or an error dict.
        """
        try:
            data = client.get_author(author_id=author_id, select=select)
        except httpx.HTTPStatusError as exc:
            return {"error": f"OpenAlex API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        return data
