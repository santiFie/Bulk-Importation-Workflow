"""
Springer MCP — HTTP Client

Wraps the Springer Nature Open Access REST API with a thin, reusable httpx client.

Springer Nature Open Access API reference:
  https://dev.springernature.com/docs/api-endpoints/open-access/
  https://dev.springernature.com/docs/supported-query-params/

Base URL  : https://api.springernature.com
Auth      : api_key=<key> query parameter (mandatory)
Endpoints :
  GET /openaccess/json  — Open Access articles in JSON format
  GET /openaccess/xml   — Open Access articles in XML format (not used here)
  GET /meta/v2/json     — Full metadata (both OA and subscription content)

Query parameters (all optional except q):
  q          Full-text / field-scoped Lucene query.
             Field prefixes: title:, doi:, issn:, isbn:, keyword:, pub:,
             subject:, type:, country:, onlinedate:, year:
  p          Page size (default 10, max 100)
  s          Start record — 1-based offset for pagination (default 1)
  subject    Subject category filter (convenience alias for q=subject:<X>)
  type       Document type filter  (e.g. "Journal Article", "Book Chapter")

Response JSON top-level keys:
  query               — echoed back query string
  result[0].total     — total matching records
  result[0].start     — current start offset
  result[0].pageLength  — page size used
  result[0].recordsDisplayed — records on this page
  records             — list of article objects
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from config import SPRINGER_BASE_URL, SPRINGER_API_KEY

logger = logging.getLogger(__name__)

# Springer API page size limits
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class SpringerClient:
    """Synchronous HTTP client for the Springer Nature Open Access API."""

    def __init__(self) -> None:
        if not SPRINGER_API_KEY:
            logger.warning(
                "SPRINGER_API_KEY is not set. All requests will fail with 401."
            )

        self._client = httpx.Client(
            base_url=SPRINGER_BASE_URL,
            timeout=30.0,
        )
        self._api_key = SPRINGER_API_KEY

    # ──────────────────────────────────────────────────────────────────────────
    # Low-level helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a GET request and return the parsed JSON body."""
        # Inject the mandatory API key
        merged: dict[str, Any] = {"api_key": self._api_key, **params}
        # Strip keys whose value is None or empty string
        merged = {k: v for k, v in merged.items() if v is not None and v != ""}
        logger.debug("GET %s  params=%s", path, {k: v for k, v in merged.items() if k != "api_key"})
        response = self._client.get(path, params=merged)
        response.raise_for_status()
        return response.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Open Access search
    # ──────────────────────────────────────────────────────────────────────────

    def search_open_access(
        self,
        q: str,
        page_size: int = DEFAULT_PAGE_SIZE,
        start: int = 1,
    ) -> dict[str, Any]:
        """
        Search the Springer Nature Open Access collection.

        Args:
            q:         Lucene-style query string.
            page_size: Number of records to return (max 100).
            start:     1-based start record for pagination.

        Returns:
            Full API response dict (keys: query, result, records, facets).
        """
        params: dict[str, Any] = {
            "q": q,
            "p": min(page_size, MAX_PAGE_SIZE),
            "s": max(1, start),
        }
        return self._get("/openaccess/json", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Full Metadata API (OA + subscription)
    # ──────────────────────────────────────────────────────────────────────────

    def search_metadata(
        self,
        q: str,
        page_size: int = DEFAULT_PAGE_SIZE,
        start: int = 1,
    ) -> dict[str, Any]:
        """
        Search the full Springer Nature Metadata API (OA + subscription content).

        Args:
            q:         Lucene-style query string.
            page_size: Number of records to return (max 100).
            start:     1-based start record for pagination.

        Returns:
            Full API response dict.
        """
        params: dict[str, Any] = {
            "q": q,
            "p": min(page_size, MAX_PAGE_SIZE),
            "s": max(1, start),
        }
        return self._get("/meta/v2/json", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Article retrieval by DOI
    # ──────────────────────────────────────────────────────────────────────────

    def get_article_by_doi(self, doi: str) -> dict[str, Any]:
        """
        Retrieve a single Open Access article by its DOI.

        Args:
            doi: The article DOI (e.g. '10.1007/s00253-020-10496-4').

        Returns:
            Full API response dict; ``records`` will contain at most one item.
        """
        return self.search_open_access(q=f"doi:{doi}", page_size=1, start=1)
