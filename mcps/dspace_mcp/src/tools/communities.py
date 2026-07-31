"""
DSpace MCP Server — Community Tools

Covers listing, retrieving, creating and updating communities.
DSpace communities are the top-level organizational units. A community can
contain sub-communities and/or collections.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient

logger = logging.getLogger(__name__)


def _format_community(c: dict) -> dict:
    """Flatten a raw DSpace community object into a clean dict."""
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
    def list_communities(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List all communities in the DSpace repository (paginated).

        Args:
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'communities' list.
        """
        try:
            data = client.get("/api/core/communities", params={"page": page, "size": size})
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        embedded = data.get("_embedded", {}).get("communities", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "communities": [_format_community(c) for c in embedded],
        }

    @mcp.tool()
    def list_top_communities(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List only top-level communities (those without a parent community).

        Args:
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'communities' list.
        """
        try:
            data = client.get(
                "/api/core/communities/search/top",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        embedded = data.get("_embedded", {}).get("communities", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "communities": [_format_community(c) for c in embedded],
        }

    @mcp.tool()
    def get_community(uuid: str) -> dict[str, Any]:
        """
        Retrieve a single community by its UUID.

        Args:
            uuid: The UUID of the community.

        Returns:
            Community details including uuid, name, handle, metadata, and archived_items_count.
        """
        try:
            data = client.get(f"/api/core/communities/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_community(data)

    @mcp.tool()
    def get_community_collections(uuid: str, page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List collections that belong to a specific community.

        Args:
            uuid: UUID of the community.
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'collections' list.
        """
        try:
            data = client.get(
                f"/api/core/communities/{uuid}/collections",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        embedded = data.get("_embedded", {}).get("collections", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "collections": [
                {
                    "uuid": c.get("uuid"),
                    "name": c.get("name"),
                    "handle": c.get("handle"),
                    "metadata": c.get("metadata", {}),
                }
                for c in embedded
            ],
        }

    @mcp.tool()
    def get_community_subcommunities(uuid: str, page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List sub-communities within a given community.

        Args:
            uuid: UUID of the parent community.
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'communities' list.
        """
        try:
            data = client.get(
                f"/api/core/communities/{uuid}/subcommunities",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        embedded = data.get("_embedded", {}).get("communities", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "communities": [_format_community(c) for c in embedded],
        }

    @mcp.tool()
    def create_community(
        name: str,
        description: str = "",
        abstract: str = "",
        parent_uuid: str = "",
    ) -> dict[str, Any]:
        """
        Create a new community in DSpace.

        To create a top-level community, omit parent_uuid.
        To create a sub-community, provide the UUID of the parent community.

        Args:
            name: Display name of the new community.
            description: Short description (dc.description).
            abstract: Long description / abstract (dc.description.abstract).
            parent_uuid: UUID of the parent community. Leave empty for a top-level community.

        Returns:
            The created community object with its new UUID.
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

        path = "/api/core/communities"
        if parent_uuid:
            path += f"?parent={parent_uuid}"

        try:
            data = client.post(path, json=body)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_community(data)

    @mcp.tool()
    def update_community(
        uuid: str,
        name: str = "",
        description: str = "",
        abstract: str = "",
    ) -> dict[str, Any]:
        """
        Update the metadata of an existing community.

        Only the fields provided (non-empty) will be included in the update.
        The uuid, handle, and type fields are read-only in DSpace and must not be changed.

        Args:
            uuid: UUID of the community to update.
            name: New name / title (dc.title). Leave empty to keep existing.
            description: New short description (dc.description). Leave empty to keep existing.
            abstract: New abstract (dc.description.abstract). Leave empty to keep existing.

        Returns:
            The updated community object.
        """
        # Fetch current state first to avoid overwriting fields we're not updating
        try:
            current = client.get(f"/api/core/communities/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"Could not fetch current community: {exc.response.status_code}"}

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
            "type": "community",
        }

        try:
            data = client.put(f"/api/core/communities/{uuid}", json=body)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _format_community(data)
