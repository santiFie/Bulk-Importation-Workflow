"""
OpenAlex MCP — Works Tools

Exposes four tools:
  • search_works             — free-text / filter search across all works.
  • get_work                 — retrieve a single work by ID or DOI.
  • get_works_by_author      — paginated list of works for a specific author.
  • get_works_by_institution — paginated list of works affiliated to an institution.
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
    def search_works(
        search: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """
        Search OpenAlex works (papers, books, datasets, pre-prints, etc.) by
        free-text query, filter expression, or both.

        At least one of `search` or `filter` must be provided.

        Args:
            search: Optional free-text title/abstract query (e.g. 'machine learning in biology').
                    Can be omitted when `filter` alone is sufficient.
            filter: OpenAlex filter string. Examples:
                    - 'publication_year:2023'
                    - 'authorships.author.id:A2208157607'
                    - 'authorships.institutions.id:I33213144'
                    - 'open_access.is_oa:true'
                    - 'type:article'
                    Full list at: https://docs.openalex.org/api-reference/works/filter-works
            select: Comma-separated field names to return per work.
                    Common fields: id, doi, display_name, publication_year,
                    publication_date, type, cited_by_count, open_access,
                    primary_location, authorships, biblio, topics, keywords,
                    abstract_inverted_index.
                    Omit to get all default fields.
            sort: Sort expression (e.g. 'cited_by_count:desc', 'publication_year:desc').
            page: 1-based page number (default 1).
            per_page: Number of results per page, max 200 (default 25).

        Returns:
            A dict with:
              - meta.count     : total matching works
              - meta.page      : current page
              - meta.per_page  : page size
              - results        : list of work objects
        """
        try:
            data = client.search_works(
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
    def get_work(
        work_id: str,
        select: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve a single work by its OpenAlex ID or DOI.

        Args:
            work_id: OpenAlex work ID (e.g. 'W2741809807') or DOI in the
                     format 'doi:10.1038/s41586-021-03964-9'.
            select: Comma-separated list of fields to return.
                    Common fields: id, doi, display_name, publication_year,
                    publication_date, type, cited_by_count, open_access,
                    primary_location, authorships, biblio, topics, keywords,
                    abstract_inverted_index, referenced_works, related_works.
                    Omit to get all default fields.

        Returns:
            A single work object dict, or an error dict.
        """
        try:
            data = client.get_work(work_id=work_id, select=select)
        except httpx.HTTPStatusError as exc:
            return {"error": f"OpenAlex API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        return data

    @mcp.tool()
    def get_works_by_author(
        author_id: str,
        select: Optional[str] = None,
        sort: Optional[str] = "cited_by_count:desc",
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """
        Retrieve all works attributed to a specific author.

        This is a convenience wrapper that automatically sets
        filter=authorships.author.id:<author_id> on the works endpoint.

        Args:
            author_id: OpenAlex author ID (e.g. 'A2208157607'). You must resolve
                       a human-readable name to an ID first using search_authors.
            select: Comma-separated list of fields to return per work.
                    Common fields: id, doi, display_name, publication_year,
                    cited_by_count, type, primary_location, authorships,
                    open_access.
                    Omit to get all default fields.
            sort: Sort expression. Default 'cited_by_count:desc'. Other examples:
                  'publication_year:desc', 'display_name:asc'.
            page: 1-based page number (default 1).
            per_page: Number of results per page, max 200 (default 25).

        Returns:
            A dict with:
              - meta.count     : total works for this author
              - meta.page      : current page
              - meta.per_page  : page size
              - results        : list of work objects
        """
        try:
            data = client.get_works_by_author(
                author_id=author_id,
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
    def get_works_by_institution(
        institution_id: str,
        select: Optional[str] = None,
        sort: Optional[str] = "cited_by_count:desc",
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """
        Retrieve all works affiliated to a specific institution.

        This is a convenience wrapper that automatically sets
        filter=authorships.institutions.id:<institution_id> on the works endpoint.

        Args:
            institution_id: OpenAlex institution ID (e.g. 'I33213144'). You must
                            resolve a human-readable name to an ID first using
                            search_institutions.
            select: Comma-separated list of fields to return per work.
                    Common fields: id, doi, display_name, publication_year,
                    cited_by_count, type, primary_location, authorships,
                    open_access.
                    Omit to get all default fields.
            sort: Sort expression. Default 'cited_by_count:desc'. Other examples:
                  'publication_year:desc'.
            page: 1-based page number (default 1).
            per_page: Number of results per page, max 200 (default 25).

        Returns:
            A dict with:
              - meta.count     : total works for this institution
              - meta.page      : current page
              - meta.per_page  : page size
              - results        : list of work objects
        """
        try:
            data = client.get_works_by_institution(
                institution_id=institution_id,
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
