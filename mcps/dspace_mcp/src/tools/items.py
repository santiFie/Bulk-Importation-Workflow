"""
DSpace MCP Server — Item Tools

Covers listing, retrieving, creating and updating items.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient

logger = logging.getLogger(__name__)


def _format_item(item: dict) -> dict:
    return {
        "uuid": item.get("uuid"),
        "name": item.get("name"),
        "handle": item.get("handle"),
        "in_archive": item.get("inArchive"),
        "discoverable": item.get("discoverable"),
        "withdrawn": item.get("withdrawn"),
        "last_modified": item.get("lastModified"),
        "metadata": item.get("metadata", {}),
        "type": item.get("type"),
    }


def register(mcp: "FastMCP", client: "DSpaceClient") -> None:

    @mcp.tool()
    def list_items(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List archived items in the DSpace repository (paginated).
        Only returns archived, non-withdrawn items. Use search_objects for withdrawn or workspace items.

        Args:
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'items' list.
        """
        try:
            data = client.get("/api/core/items", params={"page": page, "size": size})
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        embedded = data.get("_embedded", {}).get("items", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "items": [_format_item(i) for i in embedded],
        }

    @mcp.tool()
    def get_item(uuid: str) -> dict[str, Any]:
        """
        Retrieve a single item by its UUID, including all its Dublin Core metadata.

        Args:
            uuid: The UUID of the item.

        Returns:
            Item details: uuid, name, handle, in_archive, discoverable, withdrawn, last_modified, metadata.
        """
        try:
            data = client.get(f"/api/core/items/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_item(data)

    @mcp.tool()
    def get_item_bundles(uuid: str) -> dict[str, Any]:
        """
        List the bundles of an item (e.g., ORIGINAL, THUMBNAIL, LICENSE).
        The 'ORIGINAL' bundle holds the primary content files (bitstreams).

        Args:
            uuid: The UUID of the item.

        Returns:
            A dict with 'item_uuid' and 'bundles' list. Each bundle has 'uuid', 'name', and 'bitstreams_href'.
        """
        try:
            data = client.get(f"/api/core/items/{uuid}/bundles")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        embedded = data.get("_embedded", {}).get("bundles", [])
        return {
            "item_uuid": uuid,
            "bundles": [
                {
                    "uuid": b.get("uuid"),
                    "name": b.get("name"),
                    "metadata": b.get("metadata", {}),
                    "bitstreams_href": b.get("_links", {}).get("bitstreams", {}).get("href"),
                }
                for b in embedded
            ],
        }

    @mcp.tool()
    def create_item(
        collection_uuid: str,
        name: str,
        metadata: dict[str, Any] | None = None,
        discoverable: bool = True,
    ) -> dict[str, Any]:
        """
        Create a new archived item inside a collection (bypasses the submission workflow).

        Args:
            collection_uuid: UUID of the owning collection.
            name: The item name (also used for dc.title if not present in metadata).
            metadata: Dict of Dublin Core metadata fields and their values.
            discoverable: Whether the item is searchable (default True).

        Returns:
            The created item object with its new UUID.
        """
        meta = metadata or {}
        if "dc.title" not in meta:
            meta["dc.title"] = [
                {"value": name, "language": None, "authority": None, "confidence": -1}
            ]
        body: dict[str, Any] = {
            "name": name,
            "metadata": meta,
            "inArchive": True,
            "discoverable": discoverable,
            "withdrawn": False,
            "type": "item",
        }
        try:
            data = client.post(f"/api/core/items?owningCollection={collection_uuid}", json=body)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_item(data)

    @mcp.tool()
    def update_item(
        uuid: str,
        name: str = "",
        metadata: dict[str, Any] | None = None,
        discoverable: bool | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing ARCHIVED item's metadata via a full PUT replacement.

        WARNING: This replaces the complete metadata. Fields not included will be removed.
        Fetch the current item with get_item() first if you only want to change specific fields.

        IMPORTANT: This tool only works for items that are already in the archive
        (inArchive=True). Items that are in the submission workspace or under workflow
        review CANNOT be updated via this endpoint — DSpace will return a 500 error.
        Use update_workspace_item() for workspace drafts or the PATCH on workflowitems
        for items under review.

        Args:
            uuid: UUID of the item to update.
            name: New name. Leave empty to preserve the existing name.
            metadata: Complete replacement metadata dict. If omitted, current metadata is preserved.
            discoverable: Set item visibility. None means preserve the current value.

        Returns:
            The updated item object.
        """
        try:
            current = client.get(f"/api/core/items/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"Could not fetch current item: {exc.response.status_code}"}

        # Guard: DSpace 7 raises a 500 when PUT is used on non-archived items.
        # Items in workspace (inArchive=False, withdrawn=False) or workflow
        # must be edited via the submission/workflow endpoints instead.
        if not current.get("inArchive", False):
            return {
                "error": (
                    f"Item {uuid} is not in the archive (inArchive=False). "
                    "It may be in the submission workspace or under workflow review. "
                    "Use update_workspace_item() for workspace drafts or "
                    "get_workflow_item_by_item_uuid() to locate the workflow item."
                ),
                "in_archive": False,
                "withdrawn": current.get("withdrawn"),
            }

        body = {
            "uuid": uuid,
            "handle": current.get("handle"),
            "name": name or current.get("name"),
            "metadata": metadata if metadata is not None else current.get("metadata", {}),
            "inArchive": current.get("inArchive"),
            "discoverable": discoverable if discoverable is not None else current.get("discoverable"),
            "withdrawn": current.get("withdrawn"),
            "type": "item",
        }
        try:
            data = client.put(f"/api/core/items/{uuid}", json=body)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_item(data)
