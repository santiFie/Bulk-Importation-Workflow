"""
DSpace MCP Server — Search Tools

Wraps the DSpace Discovery/Solr search endpoint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient

logger = logging.getLogger(__name__)


def register(mcp: "FastMCP", client: "DSpaceClient") -> None:

    @mcp.tool()
    def search_objects(
        query: str,
        scope: str = "",
        dso_type: str = "",
        page: int = 0,
        size: int = 10,
    ) -> dict[str, Any]:
        """
        Search DSpace objects using the Discovery (Solr) engine.

        Args:
            query: Lucene/Solr query string. Use '*' to match everything.
            scope: Optional UUID of a community or collection to restrict the search.
            dso_type: Filter by object type: 'ITEM', 'COLLECTION', or 'COMMUNITY'.
            page: Zero-based page number (default 0).
            size: Number of results per page (default 10).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', 'results' (list of objects).
            Each result contains 'type', 'uuid', 'name', and 'metadata'.
        """
        params: dict[str, Any] = {"query": query, "page": page, "size": size}
        if scope:
            params["scope"] = scope
        if dso_type:
            params["dsoType"] = dso_type

        try:
            data = client.get("/api/discover/search/objects", params=params)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        search_result = data.get("_embedded", {}).get("searchResult", {})
        objects = search_result.get("_embedded", {}).get("objects", [])
        page_info = search_result.get("page", {})

        results = []
        for obj in objects:
            inner = obj.get("_embedded", {}).get("indexableObject", {})
            results.append({
                "type": inner.get("type"),
                "uuid": inner.get("uuid"),
                "name": inner.get("name"),
                "handle": inner.get("handle"),
                "metadata": inner.get("metadata", {}),
            })

        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "results": results,
        }
