"""
OpenAlex MCP — Institutions Tools

Exposes two tools:
  • search_institutions  — free-text / filter search across all institutions.
  • get_institution       — retrieve a single institution by ID.
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
    def search_institutions(
        search: str,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """
        Search OpenAlex institutions by name or filter expression.

        Args:
            search: Free-text institution name query (e.g. 'Universidad Nacional de La Plata').
            filter: OpenAlex filter string (e.g. 'country_code:AR,type:education').
                    Combine multiple filters with commas. Full list at:
                    https://docs.openalex.org/api-reference/institutions/filter-institutions
            select: Comma-separated field names to include in each result.
                    Common fields: id, display_name, country_code, type, works_count,
                    cited_by_count, ror, homepage_url, geo, associated_institutions.
                    Omit to get all default fields.
            sort: Sort expression (e.g. 'cited_by_count:desc' or 'works_count:asc').
            page: 1-based page number (default 1).
            per_page: Number of results per page, max 200 (default 25).

        Returns:
            A dict with:
              - meta.count     : total matching institutions
              - meta.page      : current page
              - meta.per_page  : page size
              - results        : list of institution objects
        """
        try:
            data = client.search_institutions(
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
    def get_institution(
        institution_id: str,
        select: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve a single institution by its OpenAlex ID or external identifier.

        Args:
            institution_id: OpenAlex institution ID (e.g. 'I33213144') or an external
                            identifier in the format 'ror:<ROR_ID>' (e.g. 'ror:03vek6s52')
                            or 'wikidata:<QID>' (e.g. 'wikidata:Q192334').
            select: Comma-separated list of fields to return.
                    Common fields: id, display_name, country_code, type, works_count,
                    cited_by_count, ror, homepage_url, geo, associated_institutions,
                    roles, x_concepts.
                    Omit to get all default fields.

        Returns:
            A single institution object dict, or an error dict.
        """
        try:
            data = client.get_institution(institution_id=institution_id, select=select)
        except httpx.HTTPStatusError as exc:
            return {"error": f"OpenAlex API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        return data
