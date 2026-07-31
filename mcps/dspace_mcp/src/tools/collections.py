"""
DSpace MCP Server — Collection Tools

Covers listing, retrieving, creating and updating collections.
In DSpace, collections always belong to a community and contain items.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient

logger = logging.getLogger(__name__)


def _format_collection(c: dict) -> dict:
    """Flatten a raw DSpace collection object into a clean dict."""
    return {
        "uuid": c.get("uuid"),
        "name": c.get("name"),
        "handle": c.get("handle"),
        "archived_items_count": c.get("archivedItemsCount"),
        "metadata": c.get("metadata", {}),
        "type": c.get("type"),
    }


def register(mcp: "FastMCP", client: "DSpaceClient") -> None:

    @mcp.tool()
    def list_collections(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List all collections in the DSpace repository (paginated).

        Args:
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'collections' list.
        """
        try:
            data = client.get("/api/core/collections", params={"page": page, "size": size})
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        embedded = data.get("_embedded", {}).get("collections", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "collections": [_format_collection(c) for c in embedded],
        }

    @mcp.tool()
    def get_collection(uuid: str) -> dict[str, Any]:
        """
        Retrieve a single collection by its UUID.

        Args:
            uuid: The UUID of the collection.

        Returns:
            Collection details including uuid, name, handle, and metadata.
        """
        try:
            data = client.get(f"/api/core/collections/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_collection(data)

    @mcp.tool()
    def create_collection(
        community_uuid: str,
        name: str,
        description: str = "",
        abstract: str = "",
    ) -> dict[str, Any]:
        """
        Create a new collection inside a community.

        Collections in DSpace must always belong to a community.

        Args:
            community_uuid: UUID of the parent community (required).
            name: Display name of the new collection.
            description: Short description (dc.description).
            abstract: Long description / abstract (dc.description.abstract).

        Returns:
            The created collection object with its new UUID.
        """
        body: dict[str, Any] = {
            "name": name,
            "metadata": {
                "dc.title": [{"value": name, "language": None, "authority": None, "confidence": -1}],
            },
        }
        if description:
            body["metadata"]["dc.description"] = [
                {"value": description, "language": None, "authority": None, "confidence": -1}
            ]
        if abstract:
            body["metadata"]["dc.description.abstract"] = [
                {"value": abstract, "language": None, "authority": None, "confidence": -1}
            ]

        try:
            data = client.post(
                f"/api/core/collections?parent={community_uuid}",
                json=body,
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_collection(data)

    @mcp.tool()
    def update_collection(
        uuid: str,
        name: str = "",
        description: str = "",
        abstract: str = "",
    ) -> dict[str, Any]:
        """
        Update the metadata of an existing collection.

        Only the fields provided (non-empty) will be updated; the rest are preserved.

        Args:
            uuid: UUID of the collection to update.
            name: New name / title (dc.title). Leave empty to keep existing.
            description: New short description (dc.description). Leave empty to keep existing.
            abstract: New abstract (dc.description.abstract). Leave empty to keep existing.

        Returns:
            The updated collection object.
        """
        try:
            current = client.get(f"/api/core/collections/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"Could not fetch current collection: {exc.response.status_code}"}

        metadata: dict = current.get("metadata", {})

        if name:
            metadata["dc.title"] = [
                {"value": name, "language": None, "authority": None, "confidence": -1}
            ]
        if description:
            metadata["dc.description"] = [
                {"value": description, "language": None, "authority": None, "confidence": -1}
            ]
        if abstract:
            metadata["dc.description.abstract"] = [
                {"value": abstract, "language": None, "authority": None, "confidence": -1}
            ]

        body = {
            "uuid": uuid,
            "handle": current.get("handle"),
            "metadata": metadata,
            "type": "collection",
        }

        try:
            data = client.put(f"/api/core/collections/{uuid}", json=body)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_collection(data)
