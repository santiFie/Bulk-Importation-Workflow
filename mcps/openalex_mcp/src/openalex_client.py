"""
OpenAlex MCP — HTTP Client
Wraps the OpenAlex REST API with a thin, reusable httpx client.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from config import OPENALEX_BASE_URL, OPENALEX_API_KEY, OPENALEX_EMAIL

logger = logging.getLogger(__name__)

# Default page size cap to avoid enormous responses
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 200


class OpenAlexClient:
    """Synchronous HTTP client for the OpenAlex REST API."""

    def __init__(self) -> None:
        headers: dict[str, str] = {}
        if OPENALEX_API_KEY:
            headers["Authorization"] = f"Bearer {OPENALEX_API_KEY}"

        params: dict[str, str] = {}
        if OPENALEX_EMAIL:
            # Polite pool — higher rate limits
            params["mailto"] = OPENALEX_EMAIL

        self._default_params = params
        self._client = httpx.Client(
            base_url=OPENALEX_BASE_URL,
            headers=headers,
            timeout=30.0,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Low-level helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a GET request and return the JSON body as a dict."""
        merged = {**self._default_params, **params}
        # Remove keys with None values so the API doesn't receive them
        merged = {k: v for k, v in merged.items() if v is not None and v != ""}
        logger.debug("GET %s  params=%s", path, merged)
        response = self._client.get(path, params=merged)
        response.raise_for_status()
        return response.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Institutions
    # ──────────────────────────────────────────────────────────────────────────

    def search_institutions(
        self,
        search: str,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> dict[str, Any]:
        """
        Search institutions by free-text or OpenAlex filter string.

        Args:
            search: Free-text query (e.g. 'Universidad Nacional de La Plata').
            filter: OpenAlex filter expression (e.g. 'country_code:AR').
            select: Comma-separated list of fields to return (e.g. 'id,display_name,country_code').
            sort: Sort field (e.g. 'cited_by_count:desc').
            page: 1-based page number.
            per_page: Results per page (max 200).
        """
        params: dict[str, Any] = {
            "search": search,
            "filter": filter,
            "select": select,
            "sort": sort,
            "page": page,
            "per_page": min(per_page, MAX_PER_PAGE),
        }
        return self._get("/institutions", params)

    def get_institution(
        self,
        institution_id: str,
        select: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve a single institution by its OpenAlex ID, ROR, Wikidata, or other
        supported identifier (e.g. 'I123456789' or 'ror:03vek6s52').

        Args:
            institution_id: OpenAlex institution ID or external identifier.
            select: Comma-separated list of fields to return.
        """
        params: dict[str, Any] = {"select": select}
        return self._get(f"/institutions/{institution_id}", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Authors
    # ──────────────────────────────────────────────────────────────────────────

    def search_authors(
        self,
        search: str,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> dict[str, Any]:
        """
        Search authors by free-text name or OpenAlex filter string.

        Args:
            search: Free-text query (e.g. 'Carlos García').
            filter: OpenAlex filter expression (e.g. 'last_known_institutions.id:I123456789').
            select: Comma-separated list of fields to return (e.g. 'id,display_name,works_count,cited_by_count').
            sort: Sort field (e.g. 'cited_by_count:desc').
            page: 1-based page number.
            per_page: Results per page (max 200).
        """
        params: dict[str, Any] = {
            "search": search,
            "filter": filter,
            "select": select,
            "sort": sort,
            "page": page,
            "per_page": min(per_page, MAX_PER_PAGE),
        }
        return self._get("/authors", params)

    def get_author(
        self,
        author_id: str,
        select: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve a single author by their OpenAlex ID or ORCID
        (e.g. 'A2208157607' or 'orcid:0000-0002-1825-0097').

        Args:
            author_id: OpenAlex author ID or ORCID URI.
            select: Comma-separated list of fields to return.
        """
        params: dict[str, Any] = {"select": select}
        return self._get(f"/authors/{author_id}", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Works
    # ──────────────────────────────────────────────────────────────────────────

    def search_works(
        self,
        search: str,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> dict[str, Any]:
        """
        Search works (papers, books, datasets, etc.) by free-text or filter.

        Args:
            search: Free-text title/abstract query.
            filter: OpenAlex filter expression (e.g. 'authorships.author.id:A2208157607').
            select: Comma-separated list of fields to return.
            sort: Sort field (e.g. 'publication_year:desc').
            page: 1-based page number.
            per_page: Results per page (max 200).
        """
        params: dict[str, Any] = {
            "search": search,
            "filter": filter,
            "select": select,
            "sort": sort,
            "page": page,
            "per_page": min(per_page, MAX_PER_PAGE),
        }
        return self._get("/works", params)

    def get_work(
        self,
        work_id: str,
        select: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve a single work by its OpenAlex ID or DOI
        (e.g. 'W2741809807' or 'doi:10.1038/s41586-021-03964-9').

        Args:
            work_id: OpenAlex work ID or DOI URI.
            select: Comma-separated list of fields to return.
        """
        params: dict[str, Any] = {"select": select}
        return self._get(f"/works/{work_id}", params)

    def get_works_by_author(
        self,
        author_id: str,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> dict[str, Any]:
        """
        Retrieve all works attributed to a specific author.

        Args:
            author_id: OpenAlex author ID (e.g. 'A2208157607').
            select: Comma-separated list of fields to return.
            sort: Sort field (e.g. 'cited_by_count:desc').
            page: 1-based page number.
            per_page: Results per page (max 200).
        """
        params: dict[str, Any] = {
            "filter": f"authorships.author.id:{author_id}",
            "select": select,
            "sort": sort,
            "page": page,
            "per_page": min(per_page, MAX_PER_PAGE),
        }
        return self._get("/works", params)

    def get_works_by_institution(
        self,
        institution_id: str,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> dict[str, Any]:
        """
        Retrieve all works affiliated to a specific institution.

        Args:
            institution_id: OpenAlex institution ID (e.g. 'I123456789').
            select: Comma-separated list of fields to return.
            sort: Sort field (e.g. 'cited_by_count:desc').
            page: 1-based page number.
            per_page: Results per page (max 200).
        """
        params: dict[str, Any] = {
            "filter": f"authorships.institutions.id:{institution_id}",
            "select": select,
            "sort": sort,
            "page": page,
            "per_page": min(per_page, MAX_PER_PAGE),
        }
        return self._get("/works", params)
