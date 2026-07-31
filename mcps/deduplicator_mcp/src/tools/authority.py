"""
Deduplicator MCP — Authority Finder Tool

Exposes the `check_authority` MCP tool which calls POST /deduplicator/check-authority/
on the Django REST API and saves the resulting CSV to disk.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from deduplicator_client import DeduplicatorClient

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = "/app/data"


def register(mcp: "FastMCP", client: "DeduplicatorClient") -> None:

    @mcp.tool()
    def check_authority(
        resource_list_path: str,
        author_list_path: str,
        scopus: bool,
        output_filename: str = "coincidencias_autores.csv",
        output_dir: str = _DEFAULT_OUTPUT_DIR,
    ) -> dict[str, Any]:
        """
        Match authors from a document list against a reference author list,
        producing a quality-scored coincidence report (PERFECT / GOOD / REGULAR / MMM).

        Calls POST /deduplicator/check-authority/ on the Duplicate Detector Django
        service and saves the resulting CSV to disk.

        --- Input formats ---

        resource_list CSV (same format as the deduplicator):
            source,title,id,author,date
            SEDICI,Introduction to Machine Learning,123,Doe John;Smith Jane;,2020

        author_list CSV — Scopus format (scopus=True):
            Author.Name,variantes,Co-authors
            Doe John,John Doe|||J. Doe,"['Smith Jane', 'Brown Alice']"

        author_list CSV — simple format (scopus=False):
            Apellido,Nombre
            Doe,John
            Brown,Alice

        --- Output CSV columns ---
            title, id, author_coincidences

        where author_coincidences is a list of matches with quality labels.

        Args:
            resource_list_path: Absolute path to the document list CSV.
            author_list_path: Absolute path to the reference author list CSV.
            scopus: True if author_list uses Scopus format; False for simple Apellido/Nombre format.
            output_filename: Name for the output file (default "coincidencias_autores.csv").
            output_dir: Directory where the output CSV will be saved (default "/app/data").

        Returns:
            A dict with:
              - "csv_path"   — absolute path of the saved CSV file
              - "row_count"  — number of data rows in the result (header excluded)
              - "status"     — "ok" on success
            Or a dict with "error" on failure.
        """
        try:
            csv_bytes = client.check_authority(
                resource_list_path=resource_list_path,
                author_list_path=author_list_path,
                scopus=scopus,
                output_type="csv",
            )
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"Authority finder API error: {exc}"}

        return _save_csv(csv_bytes, output_dir, output_filename)


# ──────────────────────────────────────────────────────────────────────────────
# Shared helper
# ──────────────────────────────────────────────────────────────────────────────


def _save_csv(csv_bytes: bytes, output_dir: str, filename: str) -> dict[str, Any]:
    """Write *csv_bytes* to *output_dir/filename* and return a status dict."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)
        with open(output_path, "wb") as fh:
            fh.write(csv_bytes)
        row_count = max(0, len(csv_bytes.split(b"\n")) - 2)
        logger.info("Saved %d-row CSV to %s", row_count, output_path)
        return {"status": "ok", "csv_path": output_path, "row_count": row_count}
    except OSError as exc:
        return {"error": f"Failed to write output file: {exc}"}
