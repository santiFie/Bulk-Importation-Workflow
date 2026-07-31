"""
DSpace MCP Server — EPerson Tools

Covers listing and retrieving user accounts. Requires admin privileges.
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
    def list_epersons(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List user accounts (EPersons) in DSpace. Requires administrator privileges.

        Args:
            page: Zero-based page number (default 0).
            size: Number of results per page (default 20).

        Returns:
            A dict with 'total_elements', 'total_pages', 'page', and 'epersons' list.
            Each entry has 'uuid', 'email', 'name', 'netid', and 'lastActive'.
        """
        try:
            data = client.get("/api/eperson/epersons", params={"page": page, "size": size})
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        embedded = data.get("_embedded", {}).get("epersons", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "epersons": [
                {
                    "uuid": ep.get("uuid"),
                    "email": ep.get("email"),
                    "name": ep.get("name"),
                    "netid": ep.get("netid"),
                    "last_active": ep.get("lastActive"),
                    "can_log_in": ep.get("canLogIn"),
                    "require_certificate": ep.get("requireCertificate"),
                    "metadata": ep.get("metadata", {}),
                }
                for ep in embedded
            ],
        }

    @mcp.tool()
    def get_eperson(uuid: str) -> dict[str, Any]:
        """
        Retrieve a single user account (EPerson) by UUID. Requires administrator privileges.

        Args:
            uuid: The UUID of the EPerson.

        Returns:
            EPerson details: uuid, email, name, netid, last_active, can_log_in, metadata.
        """
        try:
            data = client.get(f"/api/eperson/epersons/{uuid}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}

        return {
            "uuid": data.get("uuid"),
            "email": data.get("email"),
            "name": data.get("name"),
            "netid": data.get("netid"),
            "last_active": data.get("lastActive"),
            "can_log_in": data.get("canLogIn"),
            "require_certificate": data.get("requireCertificate"),
            "metadata": data.get("metadata", {}),
        }
