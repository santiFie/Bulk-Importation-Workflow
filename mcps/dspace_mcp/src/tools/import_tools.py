"""
DSpace MCP Server — Import Tools

Provides SAF (Simple Archive Format) bulk import via the DSpace Scripts API.

The import flow is asynchronous and mirrors the export pattern:
  1. ZIP the SAF directory on disk.
  2. POST /api/system/scripts/import/processes  (multipart: zip file + parameters)
     → DSpace queues a background process and returns a processId.
  3. Poll GET /api/system/processes/{id} → wait for COMPLETED / FAILED.
  4. GET /api/system/processes/{id}/files → retrieve the mapfile content.
  5. Return the process result, mapfile content, and any log excerpts.

Script parameters (from DSpace RestContract import.md):
  -a / --add         Add items from SAF.
  -r / --replace     Replace items listed in mapfile.
  -d / --delete      Delete items listed in mapfile.
  -z / --zip         Name of the zip file to import (sent as multipart).
  -c / --collection  Handle or database ID of the destination collection.
  -m / --mapfile     Name to give the output mapfile.
  -w / --workflow    Send through collection workflow.
  -n / --notify      Send workflow notification emails.
  -v / --validate    Dry-run (validate only, do not import).
  -x / --exclude-bitstreams  Skip loading bitstream content.
  -R / --resume      Resume a failed import.
  -q / --quiet       Suppress metadata display.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from dspace_client import DSpaceClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3
_POLL_TIMEOUT_SECONDS = 600   # SAF imports can take several minutes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _zip_saf_directory(saf_dir: str) -> bytes:
    """
    Zip the entire SAF directory into an in-memory bytes buffer.

    The zip archive contains paths relative to saf_dir, e.g.:
      item_001/dublin_core.xml
      item_001/contents
      item_001/document.pdf
      item_002/dublin_core.xml
      ...
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(saf_dir):
            for fname in files:
                abs_path = os.path.join(root, fname)
                arcname = os.path.relpath(abs_path, saf_dir)
                zf.write(abs_path, arcname)
    buf.seek(0)
    return buf.read()


def _launch_import_process(
    client: "DSpaceClient",
    zip_bytes: bytes,
    zip_filename: str,
    parameters: list[dict],
) -> dict[str, Any]:
    """
    POST to /api/system/scripts/import/processes with the SAF zip and parameters.

    The multipart body must contain:
      - 'file'       : the zip file (octet-stream)
      - 'properties' : JSON array of parameter objects {"name": "-a", "value": ""}
    """
    url = f"{client.base_url}/api/system/scripts/import/processes"
    csrf = client._csrf()

    json_string = json.dumps(parameters)
    files_payload = {
        "file": (zip_filename, zip_bytes, "application/zip"),
        "properties": (None, json_string, "application/json"),
    }
    resp = client.session.post(
        url,
        files=files_payload,
        headers={"X-XSRF-TOKEN": csrf, "Content-Type": None},
    )
    if resp.status_code == 401:
        client._relogin()
        csrf = client._csrf()
        resp = client.session.post(
            url,
            files=files_payload,
            headers={"X-XSRF-TOKEN": csrf, "Content-Type": None},
        )
    resp.raise_for_status()
    return resp.json()


def _poll_until_done(
    client: "DSpaceClient",
    process_id: str,
    timeout: int,
) -> dict[str, Any]:
    """Poll process status until COMPLETED or FAILED, or until timeout."""
    deadline = time.time() + timeout
    status = "RUNNING"
    proc: dict[str, Any] = {}

    while status in ("SCHEDULED", "RUNNING"):
        if time.time() > deadline:
            return {
                "error": (
                    f"Import timed out after {timeout}s. "
                    f"Process {process_id} is still {status}. "
                    "Use get_process_status() to check later."
                ),
                "process_id": process_id,
                "status": status,
            }
        time.sleep(_POLL_INTERVAL_SECONDS)
        try:
            proc = client.get(f"/api/system/processes/{process_id}")
            status = proc.get("processStatus", "RUNNING")
        except requests.HTTPError as exc:
            return {"error": f"Failed to poll process status: {exc}"}

    return proc


def _get_process_files(
    client: "DSpaceClient",
    process_id: str,
) -> list[dict]:
    """Return the list of output files produced by the process."""
    try:
        files_data = client.get(f"/api/system/processes/{process_id}/files")
    except requests.HTTPError as exc:
        logger.warning("Could not retrieve process files: %s", exc)
        return []
    return files_data.get("_embedded", {}).get("files", [])


def _download_file_by_type(
    client: "DSpaceClient",
    files: list[dict],
    file_type: str,
) -> str | None:
    """
    Find a file by its 'type' field (e.g. 'mapfile', 'log') and download
    its text content. Returns None if the file is not found.
    """
    for f_obj in files:
        if f_obj.get("type") == file_type or file_type in f_obj.get("name", ""):
            href = f_obj.get("_links", {}).get("content", {}).get("href", "")
            if href:
                rel = href.split("/server/")[-1]
                try:
                    raw = client.get_content(rel)
                    return raw.decode("utf-8", errors="replace")
                except Exception as exc:
                    logger.warning("Failed to download %s: %s", file_type, exc)
    return None


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register(mcp: "FastMCP", client: "DSpaceClient") -> None:

    @mcp.tool()
    def import_saf_from_directory(
        saf_directory: str,
        collection_handle_or_id: str,
        mapfile_name: str = "mapfile",
        action: str = "add",
        send_through_workflow: bool = False,
        send_notifications: bool = False,
        validate_only: bool = False,
        exclude_bitstreams: bool = False,
        resume: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """
        Import items into DSpace from a local SAF directory.

        The SAF directory is zipped in memory, uploaded to the DSpace Scripts API,
        and the background import process is polled until completion.

        Args:
            saf_directory: Absolute path to the local SAF directory
                           (e.g. '/app/data/saf_output'). Must contain
                           item_001/, item_002/, ... subdirectories.
            collection_handle_or_id: Handle (e.g. '123456789/5') or database ID
                                     of the destination collection in DSpace.
            mapfile_name: Name for the output mapfile (default 'mapfile').
                          The mapfile maps each SAF item to its DSpace handle.
            action: Import action — 'add' (default), 'replace', or 'delete'.
            send_through_workflow: If True, send items through collection workflow
                                   before archiving (-w flag).
            send_notifications: If True, send workflow notification emails (-n flag).
            validate_only: If True, perform a dry-run without actually importing (-v).
            exclude_bitstreams: If True, skip loading bitstream files (-x flag).
            resume: If True, resume a previously failed import (-R flag).
            timeout_seconds: Maximum seconds to wait for the import to complete
                             (default 300; large imports may need more).

        Returns:
            On success:
                {
                  "process_id": int,
                  "status": "COMPLETED",
                  "action": str,
                  "collection": str,
                  "mapfile": str,          # mapfile content (handle per item)
                  "log_excerpt": str,      # last lines of process log
                  "validated_only": bool
                }
            On failure:
                {"error": str, "process_id": int (if known), "status": str}
        """
        if not os.path.isdir(saf_directory):
            return {"error": f"SAF directory not found: '{saf_directory}'"}

        # -- Step 1: Build parameter list for the import script ---------------
        parameters: list[dict] = []

        if action == "add":
            parameters.append({"name": "-a", "value": ""})
        elif action == "replace":
            parameters.append({"name": "-r", "value": ""})
        elif action == "delete":
            parameters.append({"name": "-d", "value": ""})
        else:
            return {"error": f"Unknown action '{action}'. Use 'add', 'replace', or 'delete'."}

        zip_filename = "saf_import.zip"
        parameters += [
            {"name": "-z", "value": zip_filename},
            {"name": "-c", "value": collection_handle_or_id},
        ]
        if send_through_workflow:
            parameters.append({"name": "-w", "value": ""})
        if send_notifications:
            parameters.append({"name": "-n", "value": ""})
        if validate_only:
            parameters.append({"name": "-v", "value": ""})
        if exclude_bitstreams:
            parameters.append({"name": "-x", "value": ""})
        if resume:
            parameters.append({"name": "-R", "value": ""})

        # -- Step 2: Zip the SAF directory ------------------------------------
        logger.info("Zipping SAF directory: %s", saf_directory)
        try:
            zip_bytes = _zip_saf_directory(saf_directory)
        except Exception as exc:
            return {"error": f"Failed to zip SAF directory: {exc}"}
        logger.info("SAF zip size: %d bytes", len(zip_bytes))

        # -- Step 3: Launch the import process --------------------------------
        logger.info(
            "Launching DSpace import process (action=%s, collection=%s)...",
            action,
            collection_handle_or_id,
        )
        try:
            proc_data = _launch_import_process(
                client, zip_bytes, zip_filename, parameters
            )
        except requests.HTTPError as exc:
            return {
                "error": (
                    f"Failed to start import process: "
                    f"{exc.response.status_code} — {exc.response.text}"
                )
            }

        process_id = str(proc_data.get("processId", ""))
        if not process_id:
            return {
                "error": "Import process started but no processId returned.",
                "raw": proc_data,
            }
        logger.info("Import process started with id=%s", process_id)

        # -- Step 4: Poll until COMPLETED or FAILED ---------------------------
        effective_timeout = min(timeout_seconds, _POLL_TIMEOUT_SECONDS)
        final_proc = _poll_until_done(client, process_id, effective_timeout)

        if "error" in final_proc:
            return final_proc

        final_status = final_proc.get("processStatus", "UNKNOWN")

        # -- Step 5: Retrieve output files (mapfile + log) --------------------
        output_files = _get_process_files(client, process_id)
        mapfile_content = _download_file_by_type(client, output_files, "mapfile")
        log_content = _download_file_by_type(client, output_files, "log")

        # Trim log to last 50 lines to keep the response size reasonable
        log_excerpt: str | None = None
        if log_content:
            lines = log_content.splitlines()
            log_excerpt = "\n".join(lines[-50:]) if len(lines) > 50 else log_content

        if final_status == "FAILED":
            return {
                "error": f"Import process {process_id} failed.",
                "process_id": int(process_id),
                "status": "FAILED",
                "log_excerpt": log_excerpt,
            }

        return {
            "process_id": int(process_id),
            "status": final_status,
            "action": action,
            "collection": collection_handle_or_id,
            "mapfile": mapfile_content,
            "log_excerpt": log_excerpt,
            "validated_only": validate_only,
            "files_found": [f.get("name") for f in output_files],
        }

    @mcp.tool()
    def validate_saf_import(
        saf_directory: str,
        collection_handle_or_id: str,
        exclude_bitstreams: bool = False,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        """
        Perform a dry-run validation of a SAF import without actually importing items.

        Equivalent to calling import_saf_from_directory() with validate_only=True.
        Useful for checking metadata format and file references before a real import.

        Args:
            saf_directory: Absolute path to the local SAF directory.
            collection_handle_or_id: Handle or ID of the destination collection.
            exclude_bitstreams: If True, skip checking bitstream file presence (-x).
            timeout_seconds: Maximum wait time (default 180).

        Returns:
            Same structure as import_saf_from_directory().
            The 'validated_only' field will always be True.
        """
        return import_saf_from_directory(
            saf_directory=saf_directory,
            collection_handle_or_id=collection_handle_or_id,
            validate_only=True,
            exclude_bitstreams=exclude_bitstreams,
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool()
    def import_saf_replace(
        saf_directory: str,
        collection_handle_or_id: str,
        mapfile_name: str = "mapfile",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """
        Replace existing DSpace items using a SAF directory and an existing mapfile.

        Use this when re-importing items that were previously imported to update
        their metadata or bitstreams. The mapfile from the original import is required
        to tell DSpace which items to replace.

        Args:
            saf_directory: Absolute path to the SAF directory.
            collection_handle_or_id: Handle or ID of the destination collection.
            mapfile_name: Name of the mapfile from the original import.
            timeout_seconds: Maximum wait time (default 300).

        Returns:
            Same structure as import_saf_from_directory().
        """
        return import_saf_from_directory(
            saf_directory=saf_directory,
            collection_handle_or_id=collection_handle_or_id,
            mapfile_name=mapfile_name,
            action="replace",
            timeout_seconds=timeout_seconds,
        )
