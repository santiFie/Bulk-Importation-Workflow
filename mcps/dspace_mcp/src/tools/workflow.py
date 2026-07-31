"""
DSpace MCP Server — Workflow Tools

Covers the complete submission & review lifecycle for items:
  1. WorkspaceItems  (/api/submission/workspaceitems)  — drafts before workflow
  2. WorkflowItems   (/api/workflow/workflowitems)     — items under review
  3. ClaimedTasks    (/api/workflow/claimedtasks)      — reviewer tasks (approve/reject)

Typical publish flow
--------------------
  create_workspace_item  →  submit_workspace_to_workflow  →
  [reviewer] claim_pool_task  →  approve_claimed_task
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_workspace(ws: dict) -> dict:
    """Return a concise, serialisable snapshot of a workspaceitem."""
    sections = ws.get("sections", {})
    page1 = sections.get("traditional-page1", {})
    return {
        "id": ws.get("id"),
        "last_modified": ws.get("lastModified"),
        "collection_uuid": sections.get("collection"),
        "title": _first_value(page1.get("dc.title")),
        "sections": sections,
        "type": ws.get("type"),
        "_links": ws.get("_links", {}),
    }


def _fmt_workflow(wf: dict) -> dict:
    """Return a concise, serialisable snapshot of a workflowitem."""
    sections = wf.get("sections", {})
    page1 = sections.get("traditional-page1", {})
    return {
        "id": wf.get("id"),
        "step": wf.get("step"),
        "last_modified": wf.get("lastModified"),
        "collection_uuid": sections.get("collection"),
        "title": _first_value(page1.get("dc.title")),
        "sections": sections,
        "type": wf.get("type"),
        "_links": wf.get("_links", {}),
    }


def _fmt_claimed(ct: dict) -> dict:
    return {
        "id": ct.get("id"),
        "step": ct.get("step"),
        "action": ct.get("action"),
        "type": ct.get("type"),
        "_links": ct.get("_links", {}),
    }


def _first_value(field) -> str | None:
    """Extract the first string value from a DSpace metadata field list."""
    if isinstance(field, list) and field:
        entry = field[0]
        if isinstance(entry, dict):
            return entry.get("value")
        return str(entry)
    return None


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------

def register(mcp: "FastMCP", client: "DSpaceClient") -> None:

    # -----------------------------------------------------------------------
    # WORKSPACE ITEMS
    # -----------------------------------------------------------------------

    @mcp.tool()
    def list_workspace_items(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List all workspace items (drafts not yet sent to workflow) for the
        currently authenticated user.

        Args:
            page: Zero-based page number (default 0).
            size: Results per page (default 20).

        Returns:
            Dict with 'total_elements', 'total_pages', 'page', and 'items' list.
            Each item contains: id, last_modified, collection_uuid, title, sections.
        """
        try:
            data = client.get(
                "/api/submission/workspaceitems",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        embedded = data.get("_embedded", {}).get("workspaceitems", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "items": [_fmt_workspace(ws) for ws in embedded],
        }

    @mcp.tool()
    def get_workspace_item(workspace_item_id: int) -> dict[str, Any]:
        """
        Retrieve a single workspace item (draft) by its numeric ID.

        Args:
            workspace_item_id: Numeric ID of the workspace item (not the item UUID).

        Returns:
            Workspace item details: id, collection_uuid, title, sections, links.
        """
        try:
            data = client.get(f"/api/submission/workspaceitems/{workspace_item_id}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _fmt_workspace(data)

    @mcp.tool()
    def get_workspace_item_by_item_uuid(item_uuid: str) -> dict[str, Any]:
        """
        Find the workspace item (draft) associated with a given item UUID.
        Returns 204/empty if the item is not currently in the workspace.

        Args:
            item_uuid: UUID of the underlying DSpace item.

        Returns:
            Workspace item details or {'status': 'not_found'} if none exists.
        """
        try:
            data = client.get(
                "/api/submission/workspaceitems/search/item",
                params={"uuid": item_uuid},
            )
        except requests.HTTPError as exc:
            if exc.response.status_code == 204:
                return {"status": "not_found", "item_uuid": item_uuid}
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _fmt_workspace(data)

    @mcp.tool()
    def create_workspace_item(
        collection_uuid: str,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new workspace item (draft submission) inside a collection.
        The item enters the workspace and must later be submitted to workflow
        via submit_workspace_to_workflow().

        Args:
            collection_uuid: UUID of the target collection.
            title: Item title (used as dc.title if not already in metadata).
            metadata: Optional dict of Dublin Core metadata fields.
                      Each key maps to a list of value dicts, e.g.:
                      {"dc.description.abstract": [{"value": "Abstract text"}]}

        Returns:
            The created workspace item with its numeric ID and sections.
        """
        meta = metadata or {}
        if "dc.title" not in meta:
            meta["dc.title"] = [{"value": title}]

        # Build a minimal sections payload for traditional submission
        body: dict[str, Any] = {
            "sections": {
                "collection": collection_uuid,
                "traditional-page1": meta,
            }
        }
        try:
            data = client.post(
                f"/api/submission/workspaceitems?owningCollection={collection_uuid}",
                json=body,
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _fmt_workspace(data)

    @mcp.tool()
    def update_workspace_item(
        workspace_item_id: int,
        metadata: dict[str, Any],
        section: str = "traditionalpageone2",
    ) -> dict[str, Any]:
        """
        Update the metadata fields of a workspace item (draft) via PATCH.

        DSpace 9 PATCH format: one 'add' operation per metadata field,
        targeting the full field path within the section.
        Example: path='/sections/traditionalpageone2/dc.title',
                 value=[{"value": "Title", "language": null, "authority": null, "confidence": -1}]

        Fetch the workspace item first to confirm the exact section name
        (SEDICI uses 'traditionalpageone2' for the main metadata section).

        Args:
            workspace_item_id: Numeric ID of the workspace item.
            metadata: Dict of Dublin Core fields to update.
                      Format: {"dc.title": [{"value": "New title", "language": None,
                                             "authority": None, "confidence": -1}],
                               "dc.contributor.author": [{"value": "Author"}]}
            section: Submission section to patch (default: 'traditionalpageone2').

        Returns:
            Updated workspace item with new sections.
        """
        # DSpace 9 PATCH: one 'add' op per field (field-level path, array value)
        patch_ops = [
            {
                "op": "add",
                "path": f"/sections/{section}/{field}",
                "value": values,
            }
            for field, values in metadata.items()
        ]
        try:
            data = client.patch(
                f"/api/submission/workspaceitems/{workspace_item_id}",
                json=patch_ops,
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} \u2014 {exc.response.text}"}
        return _fmt_workspace(data)

    @mcp.tool()
    def delete_workspace_item(workspace_item_id: int) -> dict[str, Any]:
        """
        Permanently delete a workspace item (draft) and its associated item object.
        Only the submitter or an administrator can delete a workspace item.

        Args:
            workspace_item_id: Numeric ID of the workspace item to delete.

        Returns:
            {'status': 'deleted', 'id': workspace_item_id} on success.
        """
        try:
            client.delete(f"/api/submission/workspaceitems/{workspace_item_id}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return {"status": "deleted", "id": workspace_item_id}

    # -----------------------------------------------------------------------
    # WORKFLOW ITEMS
    # -----------------------------------------------------------------------

    @mcp.tool()
    def submit_workspace_to_workflow(workspace_item_id: int) -> dict[str, Any]:
        """
        Submit a workspace item into the review workflow, creating a workflow item.
        The workspace item must be valid (all required fields filled).

        This is the "publish to workflow" action: after this call the item leaves
        the workspace and enters the configured review steps.

        If no workflow is configured for the collection the item is published
        immediately and the response body will be empty (status: 'published').

        Args:
            workspace_item_id: Numeric ID of the workspace item to submit.

        Returns:
            The created workflow item details, or {'status': 'published'} if the
            item was immediately archived (no workflow configured).
        """
        # DSpace expects a text/uri-list body pointing to the workspaceitem
        base_url = client.base_url.rstrip("/")
        workspace_uri = f"{base_url}/api/submission/workspaceitems/{workspace_item_id}"
        try:
            resp = client.post_uri_list("/api/workflow/workflowitems", uri=workspace_uri)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        # 201 with body → workflow item created; empty body → immediately published
        if not resp:
            return {"status": "published", "workspace_item_id": workspace_item_id}
        return _fmt_workflow(resp)

    @mcp.tool()
    def list_workflow_items(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List all workflow items (items currently under review) visible to the
        authenticated admin user.

        Args:
            page: Zero-based page number (default 0).
            size: Results per page (default 20).

        Returns:
            Dict with 'total_elements', 'total_pages', 'page', and 'items' list.
            Each item includes: id, step, collection_uuid, title, sections.
        """
        try:
            data = client.get(
                "/api/workflow/workflowitems",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        embedded = data.get("_embedded", {}).get("workflowitems", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "items": [_fmt_workflow(wf) for wf in embedded],
        }

    @mcp.tool()
    def get_workflow_item(workflow_item_id: int) -> dict[str, Any]:
        """
        Retrieve a single workflow item by its numeric ID.

        Args:
            workflow_item_id: Numeric ID of the workflow item.

        Returns:
            Workflow item details: id, step, collection_uuid, title, sections.
        """
        try:
            data = client.get(f"/api/workflow/workflowitems/{workflow_item_id}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _fmt_workflow(data)

    @mcp.tool()
    def get_workflow_item_by_item_uuid(item_uuid: str) -> dict[str, Any]:
        """
        Find the workflow item associated with a given item UUID.
        There is at most one workflow item per item.

        Args:
            item_uuid: UUID of the underlying DSpace item.

        Returns:
            Workflow item details or {'status': 'not_found'} if none exists.
        """
        try:
            data = client.get(
                "/api/workflow/workflowitems/search/item",
                params={"uuid": item_uuid},
            )
        except requests.HTTPError as exc:
            if exc.response.status_code == 204:
                return {"status": "not_found", "item_uuid": item_uuid}
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _fmt_workflow(data)

    @mcp.tool()
    def delete_workflow_item(workflow_item_id: int, expunge: bool = False) -> dict[str, Any]:
        """
        Reset a workflow item, sending it back to the submitter's workspace.
        If expunge=True, the workflow item (and its underlying item) is permanently deleted.

        Only administrators can perform this action.

        Args:
            workflow_item_id: Numeric ID of the workflow item.
            expunge: If True, permanently delete rather than return to workspace.

        Returns:
            {'status': 'returned_to_workspace'} or {'status': 'expunged'}.
        """
        params = {"expunge": "true"} if expunge else {}
        try:
            client.delete(
                f"/api/workflow/workflowitems/{workflow_item_id}",
                params=params,
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        status = "expunged" if expunge else "returned_to_workspace"
        return {"status": status, "id": workflow_item_id}

    # -----------------------------------------------------------------------
    # POOL TASKS  (items waiting to be claimed by a reviewer)
    # -----------------------------------------------------------------------

    @mcp.tool()
    def list_pool_tasks(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List pool tasks — workflow items waiting to be claimed by a reviewer.
        Only tasks available to the current authenticated user are returned.

        Args:
            page: Zero-based page number (default 0).
            size: Results per page (default 20).

        Returns:
            Dict with 'total_elements', 'total_pages', 'page', and 'tasks' list.
            Each task has: id, step, action, workflowitem_link.
        """
        try:
            data = client.get(
                "/api/workflow/pooltasks",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        embedded = data.get("_embedded", {}).get("pooltasks", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "tasks": [
                {
                    "id": t.get("id"),
                    "step": t.get("step"),
                    "action": t.get("action"),
                    "type": t.get("type"),
                    "workflowitem_link": t.get("_links", {}).get("workflowitem", {}).get("href"),
                }
                for t in embedded
            ],
        }

    @mcp.tool()
    def claim_pool_task(pool_task_id: int) -> dict[str, Any]:
        """
        Claim a pool task, assigning it to the current authenticated user.
        After claiming, the task becomes a 'claimedtask' and can be approved or rejected.

        Args:
            pool_task_id: Numeric ID of the pool task to claim.

        Returns:
            The created claimed task with: id, step, action, links.
        """
        base_url = client.base_url.rstrip("/")
        pool_task_uri = f"{base_url}/api/workflow/pooltasks/{pool_task_id}"
        try:
            data = client.post_uri_list("/api/workflow/claimedtasks", uri=pool_task_uri)
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return _fmt_claimed(data)

    # -----------------------------------------------------------------------
    # CLAIMED TASKS  (reviewer actions: approve / reject)
    # -----------------------------------------------------------------------

    @mcp.tool()
    def list_claimed_tasks(page: int = 0, size: int = 20) -> dict[str, Any]:
        """
        List claimed tasks for the current authenticated user (tasks they own).

        Args:
            page: Zero-based page number (default 0).
            size: Results per page (default 20).

        Returns:
            Dict with 'total_elements', 'total_pages', 'page', and 'tasks' list.
        """
        try:
            data = client.get(
                "/api/workflow/claimedtasks/search/findByUser",
                params={"page": page, "size": size},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        embedded = data.get("_embedded", {}).get("claimedtasks", [])
        page_info = data.get("page", {})
        return {
            "total_elements": page_info.get("totalElements"),
            "total_pages": page_info.get("totalPages"),
            "page": page_info.get("number", 0),
            "tasks": [_fmt_claimed(ct) for ct in embedded],
        }

    @mcp.tool()
    def approve_claimed_task(claimed_task_id: int) -> dict[str, Any]:
        """
        Approve a claimed workflow task, advancing the item to the next workflow step
        or publishing it to the archive if this is the final step.

        This is the primary "publish item" action once an item is under review.

        Args:
            claimed_task_id: Numeric ID of the claimed task to approve.

        Returns:
            {'status': 'approved', 'id': claimed_task_id} on success (HTTP 204).
        """
        try:
            client.post(
                f"/api/workflow/claimedtasks/{claimed_task_id}",
                data={"submit_approve": "true"},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return {"status": "approved", "id": claimed_task_id}

    @mcp.tool()
    def reject_claimed_task(claimed_task_id: int, reason: str) -> dict[str, Any]:
        """
        Reject a claimed workflow task, returning the item to the submitter with a reason.

        Args:
            claimed_task_id: Numeric ID of the claimed task to reject.
            reason: Mandatory rejection reason message sent to the submitter.

        Returns:
            {'status': 'rejected', 'id': claimed_task_id} on success (HTTP 204).
        """
        try:
            client.post(
                f"/api/workflow/claimedtasks/{claimed_task_id}",
                data={"submit_reject": "true", "reason": reason},
            )
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return {"status": "rejected", "id": claimed_task_id, "reason": reason}

    @mcp.tool()
    def unclaim_task(claimed_task_id: int) -> dict[str, Any]:
        """
        Release a previously claimed task back to the pool so another reviewer
        can pick it up.

        Args:
            claimed_task_id: Numeric ID of the claimed task to release.

        Returns:
            {'status': 'unclaimed', 'id': claimed_task_id} on success.
        """
        try:
            client.delete(f"/api/workflow/claimedtasks/{claimed_task_id}")
        except requests.HTTPError as exc:
            return {"error": f"DSpace API error: {exc.response.status_code} — {exc.response.text}"}
        return {"status": "unclaimed", "id": claimed_task_id}
